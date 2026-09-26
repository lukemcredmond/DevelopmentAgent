"""Safety refusal recovery: early backup switch and synthetic read path."""

from unittest.mock import patch

from backend import state
from backend.agents.scrum_agent import ScrumAgent
from backend.agents.task_context import init_new_task, set_active_sprint_context
from backend.bootstrap import initialize
from backend.services.step_diagnostics import start_step_trace


def test_safety_refusal_skips_backup_on_first_reject():
    initialize()
    agent = ScrumAgent(role="Developer", model="primary-model", system_prompt="dev")
    agent._forced_patch_step = True
    agent._mid_step_backup_switched = False

    switched = {"called": False}

    def fake_switch(**kwargs):
        switched["called"] = True
        return True

    content = "I'm sorry, but I can't assist with that request."
    assert agent._is_safety_refusal(content) is True
    with patch.object(agent, "_switch_dev_backup_model", side_effect=fake_switch):
        agent._maybe_switch_backup_on_text_reject(
            task_id="T-1",
            plan_n=0,
            text_n=1,
            reason="first Developer text-only reject",
        )
    assert switched["called"] is False


def test_non_safety_text_reject_can_switch_backup():
    initialize()
    agent = ScrumAgent(role="Developer", model="primary-model", system_prompt="dev")
    switched = {"called": False}

    def fake_switch(**kwargs):
        switched["called"] = True
        return True

    with patch.object(agent, "_switch_dev_backup_model", side_effect=fake_switch):
        agent._maybe_switch_backup_on_text_reject(
            task_id="T-1",
            plan_n=0,
            text_n=1,
            reason="first Developer text-only reject",
        )
    assert switched["called"] is True


def test_synthetic_read_uses_resolved_path_not_diagnostic():
    initialize()
    state.SHARED_BOARD.clear()
    for lane in ("Backlog", "In Progress", "Needs User", "Needs PO", "QA", "Done"):
        state.SHARED_BOARD[lane] = []

    diagnostic = (
        "warning • The URI 'package:flutter_lints/flutter.yaml' included in "
        "'/path/analysis_options.yaml' can't be found • analysis_options.yaml"
    )
    task = init_new_task(
        {
            "id": "T-SYN",
            "title": "Build main app UI with tabs",
            "description": "d",
            "status": "In Progress",
            "lastCommandDiagnostics": [{"file": diagnostic}],
            "consecutiveBadExits": 2,
        }
    )
    state.SHARED_BOARD["In Progress"] = [task]
    state.VIRTUAL_FILESYSTEM["analysis_options.yaml"] = "include: package:flutter_lints/flutter.yaml\n"
    set_active_sprint_context("T-SYN", "Developer")

    agent = ScrumAgent(role="Developer", model="m", system_prompt="dev")
    messages = []
    tools_used: set = set()
    trace = start_step_trace("T-SYN", task["title"], "Developer", "In Progress")

    class _Result:
        success = True
        tool_output = "include: package:flutter_lints/flutter.yaml\n"
        summary = "analysis_options.yaml"

    with patch("backend.agents.scrum_agent.execute_tool", return_value=_Result()) as mock_exec:
        ok = agent._execute_synthetic_read_fallback(
            messages,
            task_id="T-SYN",
            target=diagnostic,
            tools_used=tools_used,
        )

    assert ok is True
    mock_exec.assert_called()
    tool_calls = [(c[0][1], c[0][2].get("path")) for c in mock_exec.call_args_list]
    assert ("read_file", "analysis_options.yaml") in tool_calls
    assert any(
        "SYNTHETIC READ" in m.get("content", "") or "DETERMINISTIC LINT FIX" in m.get("content", "")
        for m in messages
        if m.get("role") == "system"
    )
    trace.finalize(exit_reason="completed_with_writes", lane_after="In Progress", ok=True)
