"""Richer tool decision summaries for working notes."""

from __future__ import annotations

from backend import state
from backend.agents.task_context import find_task_by_id, init_new_task
from backend.agents.tool_outcomes import (
    DECISION_SUMMARY_MAX_CHARS,
    format_tool_decision_summary,
)
from backend.bootstrap import initialize
from backend.services.tool_execution_service import _record_tool_side_effects


def test_format_run_command_ok_includes_command_and_exit():
    summary = format_tool_decision_summary(
        "run_command",
        {"command": "flutter analyze"},
        "[success exit 0]\nNo issues found!",
        success=True,
    )
    assert "run_command OK:" in summary
    assert "flutter analyze" in summary
    assert "exit 0" in summary
    assert len(summary) <= DECISION_SUMMARY_MAX_CHARS


def test_format_run_command_failed_includes_failed():
    summary = format_tool_decision_summary(
        "run_command",
        {"command": "npm test"},
        "[failed exit 1]\nAssertionError: expected true",
        success=False,
    )
    assert "run_command FAILED:" in summary
    assert "npm test" in summary
    assert "FAILED" in summary
    assert len(summary) <= DECISION_SUMMARY_MAX_CHARS


def test_format_summary_capped_for_long_command():
    long_cmd = "echo " + ("x" * 400)
    summary = format_tool_decision_summary(
        "run_command",
        {"command": long_cmd},
        "[success exit 0]\nok",
        success=True,
    )
    assert len(summary) <= DECISION_SUMMARY_MAX_CHARS
    assert summary.endswith("…") or len(summary) < DECISION_SUMMARY_MAX_CHARS


def test_record_side_effects_writes_rich_decision():
    initialize()
    state.SHARED_BOARD.clear()
    for lane in (
        "Backlog",
        "In Progress",
        "Needs User",
        "Needs PO",
        "QA",
        "Done",
        "Refinement",
        "Code Review",
    ):
        state.SHARED_BOARD[lane] = []
    task = init_new_task(
        {
            "id": "T-RICH",
            "title": "Rich notes",
            "description": "d",
            "status": "In Progress",
        }
    )
    state.SHARED_BOARD["In Progress"] = [task]

    _record_tool_side_effects(
        task_id="T-RICH",
        agent_role="Developer",
        tool_name="run_command",
        arguments={"command": "flutter analyze"},
        safe_args={"command": "flutter analyze"},
        tool_output="[success exit 0]\nNo issues found!",
        success=True,
        source="manual",
        save_memory=False,
        user_prompt="",
        memory_engine=None,
    )
    found = find_task_by_id("T-RICH")
    assert found is not None
    decisions = found.get("decisions") or []
    tool_decisions = [d for d in decisions if d.get("type") == "tool"]
    assert tool_decisions
    summary = tool_decisions[-1].get("summary") or ""
    assert "flutter analyze" in summary
    assert "OK" in summary
    assert "Used tool" not in summary


def test_prompt_includes_reuse_tool_tip():
    from backend.agents.task_context import build_task_prompt, record_task_decision

    initialize()
    state.SHARED_BOARD.clear()
    for lane in ("Backlog", "In Progress", "Needs User", "Needs PO", "QA", "Done"):
        state.SHARED_BOARD[lane] = []
    task = init_new_task(
        {
            "id": "T-TIP",
            "title": "Tip",
            "description": "d",
            "status": "In Progress",
        }
    )
    state.SHARED_BOARD["In Progress"] = [task]
    record_task_decision(
        "T-TIP",
        "Developer",
        "tool",
        "run_command OK: flutter analyze → exit 0",
        "ok",
    )
    prompt = build_task_prompt(task, "brief")
    assert "Reuse prior tool results" in prompt
    assert "PRIOR AGENT DECISIONS" in prompt
