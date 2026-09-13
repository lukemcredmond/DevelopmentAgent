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
    sidebar = (root / "frontend" / "src" / "components" / "Sidebar.tsx").read_text(
        encoding="utf-8"
    )
    assert "visit-cap cards" in sidebar
    assert "splitVisitCapBatch" in (root / "frontend" / "src" / "api" / "client.ts").read_text(
        encoding="utf-8"
    )
    readme = (root / "README.md").read_text(encoding="utf-8")
    assert "enableSplitOnStuck" in readme
    assert "auto-split" in readme.lower() or "Auto-split" in readme


def test_first_explore_budget_skips_auto_split():
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
        {"id": "T-EXPL-SKIP", "title": "Big", "description": "d", "status": "In Progress"}
    )
    task["stuckLoops"] = 1
    task["lastStepOutcome"] = {"exitReason": "explore_budget_exhausted"}
    _board_with(task)

    with patch(
        "backend.services.sprint_service.run_po_split_task",
        return_value={"added": 2, "taskId": "T-EXPL-SKIP", "taskIds": ["A", "B"]},
    ) as split_mock:
        _check_stuck_and_escalate("T-EXPL-SKIP", "In Progress", agent_key="dev")

    split_mock.assert_not_called()
    assert get_task_lane("T-EXPL-SKIP") == "In Progress"
    assert task.get("forcePatchNextDevStep") is True


def test_auto_split_after_force_patch_attempted():
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
        {"id": "T-FP-SPLIT", "title": "Big", "description": "d", "status": "In Progress"}
    )
    task["stuckLoops"] = 1
    task["forcePatchAttempted"] = True
    task["lastStepOutcome"] = {"exitReason": "patch_budget_exhausted"}
    _board_with(task)

    def _fake_split(task_id, ollama_url, guidance=""):
        parent = next(t for t in state.SHARED_BOARD["In Progress"] if t["id"] == task_id)
        state.SHARED_BOARD["In Progress"] = [
            t for t in state.SHARED_BOARD["In Progress"] if t["id"] != task_id
        ]
        parent["status"] = "Done"
        parent["splitSuperseded"] = True
        state.SHARED_BOARD.setdefault("Done", []).append(parent)
        return {"added": 2, "taskId": task_id, "taskIds": ["C1", "C2"]}

    with patch(
        "backend.services.sprint_service.run_po_split_task",
        side_effect=_fake_split,
    ) as split_mock:
        _check_stuck_and_escalate("T-FP-SPLIT", "In Progress", agent_key="dev")

    split_mock.assert_called_once()
    assert get_task_lane("T-FP-SPLIT") == "Done"


def test_latched_card_attempts_auto_split():
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
        {"id": "T-LATCH-SPLIT", "title": "Big", "description": "d", "status": "In Progress"}
    )
    task["stuckLoops"] = 1
    task["phaseCycleCapReached"] = True
    task["lastStepOutcome"] = {"exitReason": "explore_budget_exhausted"}
    _board_with(task)

    def _fake_split(task_id, ollama_url, guidance=""):
        parent = next(t for t in state.SHARED_BOARD["In Progress"] if t["id"] == task_id)
        state.SHARED_BOARD["In Progress"] = [
            t for t in state.SHARED_BOARD["In Progress"] if t["id"] != task_id
        ]
        parent["status"] = "Done"
        parent["splitSuperseded"] = True
        state.SHARED_BOARD.setdefault("Done", []).append(parent)
        return {"added": 2, "taskId": task_id, "taskIds": ["L1", "L2"]}

    with patch(
        "backend.services.sprint_service.run_po_split_task",
        side_effect=_fake_split,
    ) as split_mock:
        _check_stuck_and_escalate("T-LATCH-SPLIT", "In Progress", agent_key="dev")

    split_mock.assert_called_once()
    assert get_task_lane("T-LATCH-SPLIT") == "Done"


def test_latched_lint_attempts_auto_split():
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
        {
            "id": "T-LATCH-LINT-SPLIT",
            "title": "Lint: error • unused_import • lib/main.dart",
            "description": "D",
            "status": "In Progress",
        }
    )
    task["stuckLoops"] = 1
    task["phaseCycleCapReached"] = True
    task["lastCommandDiagnostics"] = [
        {"file": "lib/main.dart", "line": 10, "message": "unused import", "severity": "warning"}
    ]
    _board_with(task)

    def _fake_split(task_id, ollama_url, guidance=""):
        parent = next(t for t in state.SHARED_BOARD["In Progress"] if t["id"] == task_id)
        state.SHARED_BOARD["In Progress"] = [
            t for t in state.SHARED_BOARD["In Progress"] if t["id"] != task_id
        ]
        parent["status"] = "Done"
        parent["splitSuperseded"] = True
        state.SHARED_BOARD.setdefault("Done", []).append(parent)
        return {"added": 2, "taskId": task_id, "taskIds": ["F1", "F2"]}

    with patch(
        "backend.services.sprint_service.run_po_split_task",
        side_effect=_fake_split,
    ) as split_mock:
        _check_stuck_and_escalate("T-LATCH-LINT-SPLIT", "In Progress", agent_key="dev")

    split_mock.assert_called_once()
    kwargs = split_mock.call_args.kwargs
    args = split_mock.call_args.args
    guidance = kwargs.get("guidance") if "guidance" in kwargs else args[2]
    assert "regression test" in guidance
    assert get_task_lane("T-LATCH-LINT-SPLIT") == "Done"
    assert len(state.SHARED_BOARD.get("Needs User") or []) == 0


def test_recover_after_lint_unlatch_splits():
    initialize()
    reset_workflow_settings()
    save_workflow_settings(
        {
            "enableSplitOnStuck": True,
            "pauseSprintOnNeedsUser": False,
        }
    )
    task = init_new_task(
        {"id": "T-UNLATCH-SPLIT", "title": "Lint: error • x", "description": "d", "status": "In Progress"}
    )
    task["phaseCycleCapReached"] = True
    task["lintUnlatchCount"] = 1
    task["lastCommandDiagnostics"] = [
        {"file": "a.dart", "line": 1, "message": "invalid_annotation", "code": "invalid_annotation"}
    ]
    _board_with(task)

    def _fake_split(task_id, ollama_url, guidance=""):
        parent = next(t for t in state.SHARED_BOARD["In Progress"] if t["id"] == task_id)
        state.SHARED_BOARD["In Progress"] = [
            t for t in state.SHARED_BOARD["In Progress"] if t["id"] != task_id
        ]
        parent["status"] = "Done"
        parent["splitSuperseded"] = True
        state.SHARED_BOARD.setdefault("Done", []).append(parent)
        return {"added": 2, "taskId": task_id, "taskIds": ["U1", "U2"]}

    from backend.services.sprint_service import _recover_latched_dev_card

    with patch(
        "backend.services.sprint_service.run_po_split_task",
        side_effect=_fake_split,
    ) as split_mock:
        _recover_latched_dev_card(task, "brief")

    split_mock.assert_called_once()
    assert get_task_lane("T-UNLATCH-SPLIT") == "Done"
    assert len(state.SHARED_BOARD.get("Needs User") or []) == 0


def test_visit_cap_batch_selects_only_phase_cycle_cap():
    initialize()
    reset_workflow_settings()
    cap = init_new_task(
        {"id": "T-CAP", "title": "Latched", "description": "d", "status": "Needs User"}
    )
    cap["needsUserKind"] = "phase_cycle_cap"
    cap["userQuestion"] = "Split the card or reset the Developer visit latch."
    other = init_new_task(
        {"id": "T-ASK", "title": "Question", "description": "d", "status": "Needs User"}
    )
    other["needsUserKind"] = "po_limit"
    other["userQuestion"] = "Which screen should this land on?"
    state.SHARED_BOARD = {
        "Backlog": [],
        "In Progress": [],
        "Needs PO": [],
        "Needs User": [cap, other],
        "QA": [],
        "Done": [],
        "Features": [],
        "Refinement": [],
        "Code Review": [],
        "Blocked": [],
    }
    from backend.services.sprint_service import visit_cap_needs_user_task_ids, split_visit_cap_batch

    assert visit_cap_needs_user_task_ids() == ["T-CAP"]
    assert visit_cap_needs_user_task_ids(["T-CAP", "T-ASK"]) == ["T-CAP"]

    with patch(
        "backend.services.sprint_service.run_po_split_task",
        return_value={"added": 2, "taskId": "T-CAP", "taskIds": ["N1", "N2"]},
    ) as split_mock:
        result = split_visit_cap_batch("http://localhost:11434")

    split_mock.assert_called_once()
    assert result["taskIds"] == ["T-CAP"]
    assert split_mock.call_args.args[0] == "T-CAP"
    initialize()
    reset_workflow_settings()
    task = init_new_task(
        {"id": "T-DRAIN", "title": "Big", "description": "d", "status": "In Progress"}
    )
    _board_with(task)
    from backend.services.sprint_service import drain_pending_splits, queue_pending_split

    queue_pending_split("T-DRAIN", "please split", requested_by="ui")
    assert task.get("pendingSplit")

    with patch(
        "backend.services.sprint_service.run_po_split_task",
        return_value={"added": 2, "taskId": "T-DRAIN", "taskIds": ["A", "B"]},
    ) as split_mock:
        results = drain_pending_splits("http://localhost:11434")

    split_mock.assert_called_once()
    assert results and results[0]["taskId"] == "T-DRAIN"
    assert task.get("pendingSplit") is None
