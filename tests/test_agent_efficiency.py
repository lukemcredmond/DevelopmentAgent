"""Unit tests for local-Ollama agent efficiency MVP."""

from __future__ import annotations

from backend.services.agent_efficiency import (
    apply_tool_turn_cap,
    efficiency_high,
    max_tools_per_llm_turn,
    resolve_step_model,
    should_throttle_step_recap,
)
from backend.services.llm_context import (
    _tail_tool_summaries,
    maybe_inject_unchanged_prompt_progress,
)
from backend.services.prompt_profile import get_prompt_profile
from backend.services.workflow_settings import DEFAULT_WORKFLOW_SETTINGS


def test_defaults_enable_efficiency_high():
    assert DEFAULT_WORKFLOW_SETTINGS.get("agentEfficiencyMode") == "high"
    assert DEFAULT_WORKFLOW_SETTINGS.get("enablePhaseModelRouting") is True
    assert DEFAULT_WORKFLOW_SETTINGS.get("maxToolsPerLlmTurn") == 3
    # The loop needs enough turns to explore, edit and verify; the real stop is the
    # total tool-call budget plus the wall-clock cap.
    assert DEFAULT_WORKFLOW_SETTINGS.get("maxLlmIterationsPerStep") == 30
    assert DEFAULT_WORKFLOW_SETTINGS.get("maxToolCallsPerStep") == 80
    assert DEFAULT_WORKFLOW_SETTINGS.get("maxToolFailuresPerStep") == 4
    assert DEFAULT_WORKFLOW_SETTINGS.get("enablePromptSectionRotation") is False


def test_resolve_step_model_explore_vs_patch():
    ws = {
        "agentEfficiencyMode": "high",
        "enablePhaseModelRouting": True,
        "devExploreModel": "qwen2.5-coder:7b",
        "devPatchModel": "qwen2.5-coder:14b",
    }
    explore, er = resolve_step_model(
        role="Developer",
        phase="explore",
        primary_model="qwen2.5-coder:14b",
        backup_model="qwen2.5-coder:7b",
        ws=ws,
    )
    assert explore == "qwen2.5-coder:7b"
    assert er == "phase_explore"

    patch, pr = resolve_step_model(
        role="Developer",
        phase="patch",
        primary_model="qwen2.5-coder:14b",
        ws=ws,
    )
    assert patch == "qwen2.5-coder:14b"
    assert "patch" in pr

    verify, vr = resolve_step_model(
        role="Developer",
        phase="verify",
        primary_model="qwen2.5-coder:14b",
        ws=ws,
    )
    assert verify == "qwen2.5-coder:14b"
    assert "verify" in vr

    # Non-dev ignores phase routing
    po, reason = resolve_step_model(
        role="Product Owner",
        phase="explore",
        primary_model="llama3:8b",
        ws=ws,
    )
    assert po == "llama3:8b"
    assert reason == "role_primary"


def test_resolve_step_model_falls_back_to_backup():
    ws = {
        "agentEfficiencyMode": "high",
        "enablePhaseModelRouting": True,
        "devExploreModel": "",
        "devPatchModel": "",
        "discordModelPresetFast": "qwen2.5-coder:7b",
    }
    model, reason = resolve_step_model(
        role="Developer",
        phase="explore",
        primary_model="qwen2.5-coder:14b",
        backup_model="coder-backup:7b",
        ws=ws,
    )
    assert model == "coder-backup:7b"
    assert reason == "phase_explore"


def test_resolve_step_model_avoids_gemma_for_patch():
    ws = {
        "agentEfficiencyMode": "high",
        "enablePhaseModelRouting": True,
        "devExploreModel": "",
        "devPatchModel": "",
        "discordModelPresetFast": "qwen2.5-coder:7b",
        "discordModelPresetQuality": "qwen2.5-coder:14b",
    }
    explore, _ = resolve_step_model(
        role="Developer",
        phase="explore",
        primary_model="gemma-4-q4km:26b",
        ws=ws,
    )
    assert "gemma" not in explore.lower()
    patch, _ = resolve_step_model(
        role="Developer",
        phase="patch",
        primary_model="gemma-4-q4km:26b",
        ws=ws,
    )
    assert patch == "qwen2.5-coder:14b"


def test_max_tools_per_llm_turn_by_phase():
    ws = {"agentEfficiencyMode": "high", "maxToolsPerLlmTurn": 3}
    assert max_tools_per_llm_turn(phase="explore", ws=ws) == 3
    assert max_tools_per_llm_turn(phase="patch", ws=ws) == 2
    assert max_tools_per_llm_turn(phase="verify", ws=ws) == 2

    std = {"agentEfficiencyMode": "standard", "maxToolsPerLlmTurn": 5}
    assert max_tools_per_llm_turn(phase="explore", ws=std) == 5


def test_tail_tool_summaries_never_reembeds_full_body():
    huge = "x" * 5000
    messages = [
        {"role": "tool", "name": "read_file", "content": huge},
        {"role": "assistant", "content": "ok " + ("y" * 1000)},
    ]
    lines = _tail_tool_summaries(messages, summary_chars=400)
    assert lines
    joined = "\n".join(lines)
    assert huge not in joined
    assert len(joined) < 1200
    for line in lines:
        # each digests stays near the per-item cap
        assert len(line) <= 450


def test_unchanged_prompt_inject_uses_summaries_only():
    body = "PAYLOAD" * 800
    msgs = [
        {"role": "system", "content": "task"},
        {"role": "user", "content": "do it"},
        {"role": "tool", "name": "read_file", "content": body},
    ]
    fp0, did0 = maybe_inject_unchanged_prompt_progress(msgs, iteration=1, last_fingerprint="")
    assert did0 is False
    fp1, did1 = maybe_inject_unchanged_prompt_progress(msgs, iteration=2, last_fingerprint=fp0)
    assert did1 is True
    assert fp1
    inject_msg = msgs[-1]["content"]
    assert "Recent step output" in inject_msg
    assert body not in inject_msg
    assert "PAYLOAD" in inject_msg  # truncated digest still mentions start
    assert len(inject_msg) < len(body)


def test_efficiency_high_forces_local_slm_profile():
    assert efficiency_high({"agentEfficiencyMode": "high"})
    assert get_prompt_profile({"agentEfficiencyMode": "high", "promptProfile": "full"}) == "local_slm"
    assert get_prompt_profile({"agentEfficiencyMode": "standard", "promptProfile": "full"}) == "full"


def test_step_recap_throttled_when_phase_graph_on():
    ws = {"agentEfficiencyMode": "high", "enableDevPhaseGraph": True}
    assert should_throttle_step_recap(tool_batch_index=1, phase_graph_active=True, ws=ws) is True
    # Without phase graph: skip odd batches
    assert should_throttle_step_recap(
        tool_batch_index=1, phase_graph_active=False, ws=ws
    ) is True
    assert should_throttle_step_recap(
        tool_batch_index=2, phase_graph_active=False, ws=ws
    ) is False


def test_fast_first_code_preset_keys_match_mvp():
    """Backend defaults match Fast first code MVP (promptProfile stays full; high forces local_slm)."""
    expected = {
        "agentEfficiencyMode": "high",
        "enablePhaseModelRouting": True,
        "enablePromptSectionRotation": False,
        "maxToolsPerLlmTurn": 3,
        "maxLlmIterationsPerStep": 30,
        "maxToolFailuresPerStep": 4,
        "duplicateToolPolicy": "strict",
        "enableDevPhaseGraph": True,
        "localSlmSprintPreload": True,
    }
    for key, value in expected.items():
        assert DEFAULT_WORKFLOW_SETTINGS.get(key) == value, key
    # Preset also sets promptProfile local_slm; runtime high mode already forces it.
    assert get_prompt_profile(DEFAULT_WORKFLOW_SETTINGS) == "local_slm"


def test_sync_role_primary_model_prefers_project_over_registry_default():
    """Regression: PO must not fall back to registry llama3:8b after project load."""
    from backend.agents.scrum_agent import ScrumAgent
    from backend import state

    agent = ScrumAgent(role="Product Owner", model="llama3:8b", system_prompt="x")
    assert agent._role_primary_model == "llama3:8b"
    state.PRIMARY_MODELS = {
        **(getattr(state, "PRIMARY_MODELS", None) or {}),
        "po": "llama3-uncensored:8b",
    }
    # Simulate old bug path: bootstrap only set .model, not _role_primary_model
    agent.model = "llama3-uncensored:8b"
    agent._role_primary_model = "llama3:8b"
    synced = agent.sync_role_primary_model()
    assert synced == "llama3-uncensored:8b"
    assert agent.model == "llama3-uncensored:8b"
    assert agent._role_primary_model == "llama3-uncensored:8b"


def test_tool_turn_cap_soft_rejects_excess_calls():
    ws = {"agentEfficiencyMode": "high", "maxToolsPerLlmTurn": 3}
    calls = ["a", "b", "c", "d", "e"]
    kept, deferred, cap = apply_tool_turn_cap(calls, phase="explore", ws=ws)
    assert cap == 3
    assert kept == ["a", "b", "c"]
    assert deferred == ["d", "e"]

    kept2, deferred2, cap2 = apply_tool_turn_cap(calls, phase="patch", ws=ws)
    assert cap2 == 2
    assert kept2 == ["a", "b"]
    assert deferred2 == ["c", "d", "e"]

    kept3, deferred3, _ = apply_tool_turn_cap(["only"], phase="explore", ws=ws)
    assert kept3 == ["only"]
    assert deferred3 == []
