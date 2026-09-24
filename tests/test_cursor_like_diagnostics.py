"""Cursor-like diagnostics: suggested actions, hints, and trace fields."""

from backend.services.step_diagnostics import derive_exit_reason, start_step_trace
from backend.services.sprint_service import _outcome_suggested_action


def test_suggested_action_phase_cycle_cap():
    action = _outcome_suggested_action("phase_cycle_cap", "In Progress")
    assert "split" in action.lower()
    assert "run in progress again" not in action.lower()


def test_suggested_action_text_rejection_loop_with_file():
    task = {"lintSourceFile": "pubspec.yaml", "title": "Lint: pubspec.yaml"}
    action = _outcome_suggested_action("text_rejection_loop", "In Progress", task=task)
    assert "backup model" in action.lower()
    assert "pubspec.yaml" in action
    assert "run in progress again" not in action.lower()


def test_trace_cursor_likeness_fields():
    trace = start_step_trace("T-CUR", "Lint: main.dart", "Developer", "In Progress")
    trace.log_ollama_call(
        1,
        duration_ms=100,
        tool_calls=["read_file"],
        prompt_tokens=4200,
        native_tool_calls=True,
    )
    trace.log_ollama_call(
        2,
        duration_ms=80,
        tool_calls=[],
        text_chars=48,
        prompt_tokens=4100,
        native_tool_calls=False,
    )
    trace.log_tool("read_file", True, "Read lib/main.dart (120 lines)")
    trace.log_tool("apply_patch", True, "Patched lib/main.dart (+3/-1)")
    trace.log_event("text_rejected", "sorry")
    payload = trace._build_payload(status="complete", exit_reason="completed_with_writes", ok=True)
    assert payload["promptTokensAtFirstCall"] == 4200
    assert payload["textOnlyTurns"] == 1
    assert payload["nativeToolCallRate"] == 0.5
    assert payload["cursorLikenessScore"] is True


def test_build_hint_text_rejection_loop():
    trace = start_step_trace("T-HINT", "Title", "Developer", "In Progress")
    hint = trace._build_hint("text_rejection_loop")
    assert "refused tools" in hint.lower() or "apology" in hint.lower()


def test_should_slim_dev_prompt_for_stuck_feature_card():
    from backend.services.sprint_service import _should_slim_dev_prompt

    task = {
        "title": "Build main app UI with tabs",
        "consecutiveBadExits": 2,
        "lastStepOutcome": {"exitReason": "text_rejection_loop"},
    }
    assert _should_slim_dev_prompt(task) is True


def test_force_patch_after_text_rejection_loop_exit():
    from backend.services.sprint_speed_gates import should_force_patch_next_dev_step

    task = {
        "lastStepOutcome": {"exitReason": "text_rejection_loop"},
    }
    assert should_force_patch_next_dev_step(task) is True

    reason = derive_exit_reason(
        agent_result="Stopped: text rejection loop — identical text repeated (2×).",
        tools_used=set(),
        lane_before="In Progress",
        lane_after="In Progress",
    )
    assert reason == "text_rejection_loop"
