"""Offline readiness preflight."""

from dataclasses import dataclass
from typing import List, Optional

import pytest

from backend import state
from backend.services import preflight as pf
from backend.services.preflight import STATUS_FAIL, STATUS_OK, STATUS_WARN, run_preflight


@dataclass
class FakeHealth:
    ok: bool
    url: str = "http://box:11434"
    models: Optional[List[str]] = None
    provider: str = "ollama"
    error: str = ""

    def __post_init__(self):
        if self.models is None:
            self.models = []


class FakeProvider:
    def __init__(self, health: FakeHealth):
        self._health = health

    def health(self) -> FakeHealth:
        return self._health


@pytest.fixture
def healthy_endpoint(monkeypatch):
    """Reachable server holding every configured model."""
    models = ["qwen2.5-coder:7b", "nomic-embed-text:latest"]
    monkeypatch.setattr(
        pf, "_check_llm_endpoint", lambda ws: (pf.Check("llm_endpoint", STATUS_OK, "ok"), models)
    )
    monkeypatch.setattr(state, "PRIMARY_MODELS", {"po": "qwen2.5-coder:7b", "dev": "qwen2.5-coder:7b"})
    monkeypatch.setattr(state, "WORKSPACE_DIR", str(__import__("pathlib").Path.cwd()))
    return models


def _check(result, name):
    return next(c for c in result["checks"] if c["name"] == name)


class TestEndpointChecks:
    def test_unreachable_endpoint_is_blocking(self, monkeypatch):
        from backend.services import llm_provider

        monkeypatch.setattr(
            llm_provider,
            "get_chat_provider",
            lambda *a, **k: FakeProvider(FakeHealth(ok=False, error="connection refused")),
        )
        result = run_preflight({})
        assert result["ready"] is False
        assert result["status"] == STATUS_FAIL
        assert _check(result, "llm_endpoint")["status"] == STATUS_FAIL

    def test_role_model_check_skipped_when_endpoint_down(self, monkeypatch):
        monkeypatch.setattr(
            pf,
            "_check_llm_endpoint",
            lambda ws: (pf.Check("llm_endpoint", STATUS_FAIL, "down"), []),
        )
        result = run_preflight({})
        assert "Skipped" in _check(result, "role_models")["detail"]

    def test_missing_role_model_is_blocking(self, monkeypatch, healthy_endpoint):
        monkeypatch.setattr(state, "PRIMARY_MODELS", {"dev": "not-pulled:70b"})
        result = run_preflight({"enableSemanticSearch": False})
        check = _check(result, "role_models")
        assert check["status"] == STATUS_FAIL
        # The fix must be runnable as-is on the inference host.
        assert "ollama pull not-pulled:70b" in check["fix"]

    def test_all_models_present_passes(self, healthy_endpoint):
        result = run_preflight({"enableSemanticSearch": False})
        assert _check(result, "role_models")["status"] == STATUS_OK

    def test_model_matches_without_explicit_tag(self, monkeypatch, healthy_endpoint):
        monkeypatch.setattr(state, "PRIMARY_MODELS", {"dev": "qwen2.5-coder"})
        result = run_preflight({"enableSemanticSearch": False})
        assert _check(result, "role_models")["status"] == STATUS_OK


class TestDegradedNotBlocking:
    """Optional services must warn, never block: the agent still works without them."""

    def test_missing_embed_model_only_warns(self, monkeypatch, healthy_endpoint):
        result = run_preflight({"enableSemanticSearch": True, "embedModel": "absent-embed"})
        assert _check(result, "embed_model")["status"] == STATUS_WARN
        assert result["ready"] is True

    def test_unreachable_qdrant_only_warns(self, healthy_endpoint):
        result = run_preflight(
            {"enableSemanticSearch": True, "qdrantUrl": "http://127.0.0.1:59999"}
        )
        assert _check(result, "qdrant")["status"] == STATUS_WARN
        assert result["ready"] is True

    def test_semantic_search_disabled_is_clean(self, healthy_endpoint):
        result = run_preflight({"enableSemanticSearch": False})
        assert _check(result, "qdrant")["status"] == STATUS_OK
        assert _check(result, "embed_model")["status"] == STATUS_OK


class TestOfflineSafety:
    def test_flags_internet_dependent_features(self, healthy_endpoint):
        result = run_preflight({"enableSemanticSearch": False, "enableWebSearch": True})
        check = _check(result, "offline_safety")
        assert check["status"] == STATUS_WARN
        assert "web search" in check["detail"]

    def test_local_mcp_server_is_not_flagged(self, healthy_endpoint):
        result = run_preflight(
            {
                "enableSemanticSearch": False,
                "mcpServers": [{"name": "local", "url": "http://localhost:9000"}],
            }
        )
        assert _check(result, "offline_safety")["status"] == STATUS_OK

    def test_remote_mcp_server_is_flagged(self, healthy_endpoint):
        result = run_preflight(
            {
                "enableSemanticSearch": False,
                "mcpServers": [{"name": "remote", "url": "https://api.example.com/mcp"}],
            }
        )
        assert _check(result, "offline_safety")["status"] == STATUS_WARN

    def test_clean_offline_setup_reports_ready(self, healthy_endpoint):
        result = run_preflight(
            {
                "enableSemanticSearch": False,
                "llmHostVramMb": 12288,
                "ollamaKvCacheType": "q8_0",
            }
        )
        assert result["status"] == STATUS_OK
        assert result["summary"] == "Ready for offline work"


class TestCapacityAndKvHints:
    def test_unknown_remote_vram_warns_with_actionable_fix(self, healthy_endpoint):
        result = run_preflight(
            {"enableSemanticSearch": False, "llmBaseUrl": "http://192.168.1.50:11434"}
        )
        check = _check(result, "inference_capacity")
        assert check["status"] == STATUS_WARN
        assert "llmHostVramMb" in check["fix"]

    def test_known_vram_passes(self, healthy_endpoint):
        result = run_preflight({"enableSemanticSearch": False, "llmHostVramMb": 12288})
        assert _check(result, "inference_capacity")["status"] == STATUS_OK

    def test_f16_kv_cache_warns(self, healthy_endpoint):
        result = run_preflight({"enableSemanticSearch": False, "ollamaKvCacheType": "f16"})
        check = _check(result, "kv_cache")
        assert check["status"] == STATUS_WARN
        assert "OLLAMA_KV_CACHE_TYPE" in check["fix"]


class TestNeverRaises:
    def test_broken_settings_do_not_raise(self, healthy_endpoint):
        """A preflight that crashes is worse than one that reports problems."""
        result = run_preflight({"mcpServers": "not-a-list", "customTools": 5})
        assert "status" in result

    def test_missing_workspace_is_blocking(self, monkeypatch, healthy_endpoint):
        monkeypatch.setattr(state, "WORKSPACE_DIR", "/nonexistent/path/xyz")
        result = run_preflight({"enableSemanticSearch": False})
        assert _check(result, "workspace")["status"] == STATUS_FAIL
        assert result["ready"] is False
