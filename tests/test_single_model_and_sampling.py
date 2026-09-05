"""Single-model routing, keep-alive policy, and per-role sampling."""

import pytest

from backend import state
from backend.services.agent_efficiency import (
    effective_keep_alive,
    phase_model_routing_enabled,
    resolve_step_model,
    single_model_mode_active,
    single_model_name,
)
from backend.services.sampling import sampling_options_for_role
from backend.services.workflow_settings import DEFAULT_WORKFLOW_SETTINGS


@pytest.fixture
def dev_primary(monkeypatch):
    monkeypatch.setattr(
        state,
        "PRIMARY_MODELS",
        {"po": "llama3:8b", "dev": "qwen2.5-coder:7b", "cr": "x:1b", "qa": "y:1b"},
    )


class TestSingleModelMode:
    def test_default_is_auto(self):
        assert DEFAULT_WORKFLOW_SETTINGS.get("singleModelMode") == "auto"

    def test_explicit_on_and_off(self):
        assert single_model_mode_active({"singleModelMode": "on"}) is True
        assert single_model_mode_active({"singleModelMode": "off"}) is False

    def test_auto_engages_on_small_known_vram(self):
        ws = {"singleModelMode": "auto", "llmBaseUrl": "http://box:11434", "llmHostVramMb": 12288}
        assert single_model_mode_active(ws) is True

    def test_auto_stays_off_with_ample_vram(self):
        ws = {"singleModelMode": "auto", "llmBaseUrl": "http://box:11434", "llmHostVramMb": 24576}
        assert single_model_mode_active(ws) is False

    def test_auto_stays_off_when_capacity_unknown(self):
        """Never change routing based on a guess about an unmeasurable remote host."""
        ws = {"singleModelMode": "auto", "llmBaseUrl": "http://box:11434"}
        assert single_model_mode_active(ws) is False

    def test_all_roles_and_phases_share_one_model(self, dev_primary):
        ws = {"singleModelMode": "on", "enablePhaseModelRouting": True}
        chosen = {
            resolve_step_model(role=role, phase=phase, primary_model="whatever", ws=ws)[0]
            for role in ("Developer", "Product Owner", "Code Reviewer", "QA Tester")
            for phase in ("explore", "patch", "verify", None)
        }
        assert chosen == {"qwen2.5-coder:7b"}

    def test_reason_is_reported_as_single_model(self, dev_primary):
        _, reason = resolve_step_model(
            role="Developer", phase="explore", primary_model="a:1b", ws={"singleModelMode": "on"}
        )
        assert reason == "single_model"

    def test_phase_routing_disabled_under_single_model(self):
        ws = {"singleModelMode": "on", "enablePhaseModelRouting": True}
        assert phase_model_routing_enabled(ws) is False

    def test_falls_back_to_supplied_primary_when_state_empty(self, monkeypatch):
        monkeypatch.setattr(state, "PRIMARY_MODELS", {})
        assert single_model_name({}, fallback="fallback:7b") == "fallback:7b"

    def test_phase_routing_still_works_when_disabled(self, dev_primary):
        """Regression guard: single-model mode must not break normal routing."""
        ws = {
            "singleModelMode": "off",
            "enablePhaseModelRouting": True,
            "devExploreModel": "small:3b",
            "devPatchModel": "big:14b",
        }
        explore, _ = resolve_step_model(
            role="Developer", phase="explore", primary_model="big:14b", ws=ws
        )
        assert explore == "small:3b"


class TestKeepAlive:
    def test_single_model_holds_indefinitely(self):
        # Nothing to evict, so a reload should never happen.
        # Ollama requires a duration unit ("-1" is a 400).
        assert effective_keep_alive({"singleModelMode": "on", "ollamaKeepAlive": "30m"}) == "-1s"

    def test_bare_minus_one_and_seconds_get_a_unit(self):
        from backend.services.agent_efficiency import normalize_ollama_keep_alive

        assert normalize_ollama_keep_alive("-1") == "-1s"
        assert normalize_ollama_keep_alive(-1) == "-1s"
        assert normalize_ollama_keep_alive("300") == "300s"
        assert normalize_ollama_keep_alive("45m") == "45m"

    def test_small_host_with_multiple_models_releases_promptly(self):
        ws = {
            "singleModelMode": "off",
            "ollamaKeepAlive": "30m",
            "llmBaseUrl": "http://box:11434",
            "llmHostVramMb": 12288,
        }
        assert effective_keep_alive(ws) == "5m"

    def test_configured_value_respected_when_capacity_unknown(self):
        ws = {"singleModelMode": "off", "ollamaKeepAlive": "45m", "llmBaseUrl": "http://box:11434"}
        assert effective_keep_alive(ws) == "45m"


class TestSampling:
    def test_defaults_are_not_greedy(self):
        """Greedy decoding drives the repetition loops the echo guards were catching."""
        opts = sampling_options_for_role("Developer", ws={})
        assert opts["temperature"] > 0
        assert opts["repeat_penalty"] > 1.0
        assert 0 < opts["top_p"] <= 1.0

    def test_po_is_more_exploratory_than_dev(self):
        po = sampling_options_for_role("Product Owner", ws={})
        dev = sampling_options_for_role("Developer", ws={})
        assert po["temperature"] > dev["temperature"]

    def test_po_sampling_num_predict_default_is_2048(self):
        po = sampling_options_for_role("Product Owner", ws={})
        assert po["num_predict"] == 2048

    def test_global_override_applies(self):
        opts = sampling_options_for_role("Developer", ws={"samplingDefaults": {"temperature": 0.7}})
        assert opts["temperature"] == 0.7

    def test_role_override_beats_global(self):
        ws = {
            "samplingDefaults": {"temperature": 0.7},
            "samplingByRole": {"dev": {"temperature": 0.05}},
        }
        assert sampling_options_for_role("Developer", ws=ws)["temperature"] == 0.05
        assert sampling_options_for_role("QA Tester", ws=ws)["temperature"] == 0.7

    def test_unknown_role_gets_defaults(self):
        opts = sampling_options_for_role("Nobody", ws={})
        assert "temperature" in opts and "repeat_penalty" in opts

    def test_garbage_override_is_ignored(self):
        ws = {"samplingByRole": {"dev": {"temperature": "hot"}}}
        assert isinstance(sampling_options_for_role("Developer", ws=ws)["temperature"], float)

    def test_top_k_coerced_to_int(self):
        opts = sampling_options_for_role("Developer", ws={"samplingDefaults": {"top_k": "40"}})
        assert opts["top_k"] == 40
