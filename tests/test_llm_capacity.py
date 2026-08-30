"""Tests for inference-host capacity detection and VRAM-fit context sizing."""

import pytest

from backend.services.llm_capacity import (
    InferenceCapacity,
    endpoint_is_local,
    fit_num_ctx,
    kv_bytes_per_token,
    resolve_inference_capacity,
    weights_bytes,
)

# Shape of a qwen2.5-coder:7b /api/show payload (trimmed to the fields we use).
QWEN_7B_META = {
    "details": {"parameter_size": "7.6B", "quantization_level": "Q4_K_M"},
    "model_info": {
        "general.parameter_count": 7615616512,
        "qwen2.block_count": 28,
        "qwen2.attention.head_count": 28,
        "qwen2.attention.head_count_kv": 4,
        "qwen2.attention.key_length": 128,
        "qwen2.attention.value_length": 128,
        "qwen2.context_length": 32768,
        "qwen2.embedding_length": 3584,
    },
}

# qwen2.5-coder:14b — the model the repo shipped as the Developer default.
QWEN_14B_META = {
    "details": {"parameter_size": "14.8B", "quantization_level": "Q4_K_M"},
    "model_info": {
        "general.parameter_count": 14770033664,
        "qwen2.block_count": 48,
        "qwen2.attention.head_count": 40,
        "qwen2.attention.head_count_kv": 8,
        "qwen2.attention.key_length": 128,
        "qwen2.attention.value_length": 128,
        "qwen2.context_length": 32768,
        "qwen2.embedding_length": 5120,
    },
}


class TestEndpointIsLocal:
    @pytest.mark.parametrize(
        "url",
        [
            "http://localhost:11434",
            "http://127.0.0.1:11434",
            "http://[::1]:1234/v1",
            "",
            "localhost:11434",
        ],
    )
    def test_local_endpoints(self, url):
        assert endpoint_is_local(url) is True

    @pytest.mark.parametrize(
        "url",
        [
            "http://192.168.1.50:11434",
            "http://gpu-box.lan:11434",
            "https://ollama.internal:443",
        ],
    )
    def test_remote_endpoints(self, url):
        assert endpoint_is_local(url) is False


class TestResolveInferenceCapacity:
    def test_remote_endpoint_never_uses_local_gpu(self, monkeypatch):
        """The bug this guards: measuring the laptop and applying it to a LAN server."""
        called = {"probed": False}

        def _should_not_run():
            called["probed"] = True
            return {"vramMb": 4096, "tier": "tiny"}

        monkeypatch.setattr(
            "backend.services.system_capacity.probe_system_capacity", _should_not_run
        )
        cap = resolve_inference_capacity({"llmBaseUrl": "http://192.168.1.50:11434"})
        assert cap.is_local is False
        assert cap.known is False
        assert cap.source == "unknown"
        assert called["probed"] is False

    def test_manual_override_wins_for_remote_host(self):
        cap = resolve_inference_capacity(
            {"llmBaseUrl": "http://192.168.1.50:11434", "llmHostVramMb": 24576}
        )
        assert cap.known is True
        assert cap.vram_mb == 24576
        assert cap.source == "manual_override"
        assert cap.is_local is False

    def test_local_endpoint_probes_this_machine(self, monkeypatch):
        monkeypatch.setattr(
            "backend.services.system_capacity.probe_system_capacity",
            lambda: {"vramMb": 12288, "ramGb": 32.0, "tier": "low"},
        )
        cap = resolve_inference_capacity({"llmBaseUrl": "http://localhost:11434"})
        assert cap.is_local is True
        assert cap.vram_mb == 12288
        assert cap.source == "local_probe"

    def test_unprobeable_local_host_is_unknown(self, monkeypatch):
        monkeypatch.setattr(
            "backend.services.system_capacity.probe_system_capacity",
            lambda: {"vramMb": None, "tier": "cpu_only"},
        )
        cap = resolve_inference_capacity({"llmBaseUrl": "http://localhost:11434"})
        assert cap.known is False


class TestKvAndWeightsMath:
    def test_kv_bytes_per_token_f16(self):
        # 28 blocks * 4 kv heads * (128 + 128) * 2 bytes = 57344
        assert kv_bytes_per_token(QWEN_7B_META, kv_cache_type="f16") == 57344

    def test_q8_cache_halves_kv_cost(self):
        f16 = kv_bytes_per_token(QWEN_7B_META, kv_cache_type="f16")
        q8 = kv_bytes_per_token(QWEN_7B_META, kv_cache_type="q8_0")
        assert q8 == f16 // 2

    def test_kv_falls_back_to_embedding_length(self):
        meta = {
            "model_info": {
                "llama.block_count": 32,
                "llama.attention.head_count": 32,
                "llama.attention.head_count_kv": 8,
                "llama.embedding_length": 4096,
            }
        }
        # head_dim = 4096/32 = 128 -> 32 * 8 * 256 * 2
        assert kv_bytes_per_token(meta, kv_cache_type="f16") == 131072

    def test_kv_returns_none_when_metadata_missing(self):
        assert kv_bytes_per_token({"model_info": {}}) is None

    def test_weights_prefers_reported_size(self):
        assert weights_bytes({"size": 4_700_000_000}) == 4_700_000_000

    def test_weights_estimated_from_parameter_count(self):
        est = weights_bytes(QWEN_7B_META)
        # ~7.6B params at Q4_K_M lands near 4.3 GB
        assert 3.5e9 < est < 5.0e9


class TestFitNumCtx:
    def test_14b_does_not_fit_12gb_at_32k(self):
        """The shipped default: 14B + 32k KV needs far more than a 12 GB card."""
        result = fit_num_ctx(32768, vram_mb=12288, model_meta=QWEN_14B_META, kv_cache_type="f16")
        assert result["clamped"] is True
        assert result["numCtx"] < 32768

    def test_7b_fits_comfortably_at_16k_on_12gb(self):
        result = fit_num_ctx(16384, vram_mb=12288, model_meta=QWEN_7B_META, kv_cache_type="q8_0")
        assert result["clamped"] is False
        assert result["numCtx"] == 16384
        assert result["fitsInVram"] is True

    def test_q8_cache_allows_more_context_than_f16(self):
        f16 = fit_num_ctx(65536, vram_mb=12288, model_meta=QWEN_14B_META, kv_cache_type="f16")
        q8 = fit_num_ctx(65536, vram_mb=12288, model_meta=QWEN_14B_META, kv_cache_type="q8_0")
        assert q8["numCtx"] > f16["numCtx"]

    def test_weights_larger_than_vram_reports_offload(self):
        result = fit_num_ctx(32768, vram_mb=4096, model_meta=QWEN_14B_META, kv_cache_type="f16")
        assert result["fitsInVram"] is False
        assert "exceed usable VRAM" in result["reason"]
        assert result["numCtx"] == 4096

    def test_unknown_capacity_passes_request_through(self):
        result = fit_num_ctx(32768, vram_mb=None, model_meta=QWEN_7B_META)
        assert result["numCtx"] == 32768
        assert result["clamped"] is False

    def test_unknown_model_metadata_passes_request_through(self):
        result = fit_num_ctx(32768, vram_mb=12288, model_meta=None)
        assert result["numCtx"] == 32768
        assert result["clamped"] is False

    def test_clamped_value_is_1024_aligned(self):
        result = fit_num_ctx(65536, vram_mb=12288, model_meta=QWEN_14B_META, kv_cache_type="f16")
        assert result["numCtx"] % 1024 == 0

    def test_never_clamps_below_usable_floor(self):
        result = fit_num_ctx(32768, vram_mb=5000, model_meta=QWEN_14B_META, kv_cache_type="f16")
        assert result["numCtx"] >= 4096


class TestCapacityDataclass:
    def test_known_requires_positive_vram(self):
        assert InferenceCapacity(vram_mb=0).known is False
        assert InferenceCapacity(vram_mb=None).known is False
        assert InferenceCapacity(vram_mb=8192).known is True
