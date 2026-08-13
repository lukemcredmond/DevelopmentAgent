"""Stuck recovery: backup first, then one auto-split before Needs PO."""

from __future__ import annotations

from unittest.mock import patch

from backend import state
from backend.agents.task_context import get_task_lane, init_new_task
from backend.bootstrap import initialize
from backend.services.backup_model import should_arm_from_exit_reason
from backend.services.sprint_service import _check_stuck_and_escalate
from backend.services.workflow_settings import (
    DEFAULT_WORKFLOW_SETTINGS,
    reset_workflow_settings,
    save_workflow_settings,
)


def _board_with(task):
    state.SHARED_BOARD = {
        "Backlog": [],
        "In Progress": [task],
        "Needs PO": [],
        "Needs User": [],
        "QA": [],
        "Done": [],
        "Features": [],
        "Refinement": [],
        "Code Review": [],
        "Blocked": [],
    }


def test_arm_exit_reasons_include_loop_stops():
    assert should_arm_from_exit_reason("duplicate_tool")
    assert should_arm_from_exit_reason("step_timeout")
    assert should_arm_from_exit_reason("tool_failure_stop")
    assert should_arm_from_exit_reason("max_iterations")
    assert not should_arm_from_exit_reason("completed_with_writes")


def test_added_children_without_parent_retirement_is_not_split_success():
    initialize()
    reset_workflow_settings()
    save_workflow_settings(
        {
            "maxStuckSteps": 2,
            "maxPoRoundTrips": 3,
            "enableSplitOnStuck": True,
            "enableBackupModelOnStuck": True,
        }
    )
    task = init_new_task(
        {"id": "T-SPLIT-OK", "title": "Big", "description": "d", "status": "In Progress"}
    )
    task["stuckLoops"] = 1
    _board_with(task)

    with patch(
        "backend.services.sprint_service.run_po_split_task",
        return_value={"added": 2, "taskId": "T-SPLIT-OK", "taskIds": ["T-A", "T-B"]},
    ) as split_mock:
        _check_stuck_and_escalate("T-SPLIT-OK", "In Progress", agent_key="dev")

    split_mock.assert_called_once()
    assert get_task_lane("T-SPLIT-OK") == "Needs PO"
    refreshed = next(t for t in state.SHARED_BOARD["Needs PO"] if t["id"] == "T-SPLIT-OK")
    assert refreshed.get("splitAttemptedOnStuck") is True


def test_split_zero_added_escalates_to_needs_po():
    initialize()
    reset_workflow_settings()
    save_workflow_settings(
        {
            "maxStuckSteps": 2,
            "maxPoRoundTrips": 3,
            "enableSplitOnStuck": True,
        }
    )
    task = init_new_task(
        {"id": "T-SPLIT-0", "title": "Big", "description": "d", "status": "In Progress"}
    )
    task["stuckLoops"] = 1
    _board_with(task)

    with patch(
        "backend.services.sprint_service.run_po_split_task",
        return_value={"added": 0, "taskId": "T-SPLIT-0", "taskIds": []},
    ):
        _check_stuck_and_escalate("T-SPLIT-0", "In Progress", agent_key="dev")

    assert get_task_lane("T-SPLIT-0") == "Needs PO"
    refreshed = next(t for t in state.SHARED_BOARD["Needs PO"] if t["id"] == "T-SPLIT-0")
    assert refreshed.get("splitAttemptedOnStuck") is True


def test_enable_split_on_stuck_false_skips_split():
    initialize()
    reset_workflow_settings()
    save_workflow_settings(
        {
            "maxStuckSteps": 2,
            "maxPoRoundTrips": 3,
            "enableSplitOnStuck": False,
        }
    )
    task = init_new_task(
        {"id": "T-NO-SPLIT", "title": "Big", "description": "d", "status": "In Progress"}
    )
    task["stuckLoops"] = 1
    _board_with(task)

    with patch(
        "backend.services.sprint_service.run_po_split_task",
        return_value={"added": 5, "taskId": "T-NO-SPLIT", "taskIds": []},
    ) as split_mock:
        _check_stuck_and_escalate("T-NO-SPLIT", "In Progress", agent_key="dev")

    split_mock.assert_not_called()
    assert get_task_lane("T-NO-SPLIT") == "Needs PO"


def test_lint_tool_stuck_skips_auto_split():
    initialize()
    reset_workflow_settings()
    save_workflow_settings(
        {
            "maxStuckSteps": 2,
            "maxPoRoundTrips": 3,
            "enableSplitOnStuck": True,
        }
    )
    task = init_new_task(
        {"id": "T-LINT-SPLIT", "title": "T", "description": "D", "status": "In Progress"}
    )
    task["stuckLoops"] = 1
    task["lastCommandDiagnostics"] = [
        {"file": "lib/main.dart", "line": 10, "message": "unused import", "severity": "warning"}
    ]
    _board_with(task)

    with patch(
        "backend.services.sprint_service.run_po_split_task",
        return_value={"added": 2, "taskId": "T-LINT-SPLIT", "taskIds": ["X"]},
    ) as split_mock:
        _check_stuck_and_escalate("T-LINT-SPLIT", "In Progress", agent_key="dev")

    split_mock.assert_not_called()
    assert get_task_lane("T-LINT-SPLIT") == "Needs PO"


def test_defaults_and_ui_markers_split_on_stuck():
    from pathlib import Path

    assert DEFAULT_WORKFLOW_SETTINGS.get("enableSplitOnStuck") is True
    root = Path(__file__).resolve().parents[1]
    wf = (root / "frontend" / "src" / "components" / "WorkflowPanel.tsx").read_text(
        encoding="utf-8"
    )
    assert "enableSplitOnStuck" in wf
    readme = (root / "README.md").read_text(encoding="utf-8")
    assert "enableSplitOnStuck" in readme
    assert "auto-split" in readme.lower() or "Auto-split" in readme
