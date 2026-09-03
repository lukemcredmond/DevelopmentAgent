"""End-to-end safety regressions for capped Developer cards."""

from unittest.mock import patch

import pytest

from backend import state
from backend.agents.task_context import get_task_lane, init_new_task
from backend.bootstrap import initialize
from backend.services.board_service import move_board_stage
from backend.services.sprint_service import run_in_progress_step, run_sprint_step
from backend.services.workflow_settings import reset_workflow_settings, save_workflow_settings


def _empty_board() -> None:
    state.SHARED_BOARD = {
        lane: []
        for lane in (
            "Backlog",
            "In Progress",
            "Needs PO",
            "Needs User",
            "QA",
            "Done",
            "Features",
            "Refinement",
            "Code Review",
            "Blocked",
        )
    }


def test_latched_card_cannot_be_run_manually_without_override():
    initialize()
    _empty_board()
    task = init_new_task(
        {"id": "T-CAPPED", "title": "Capped", "description": "d", "status": "In Progress"}
    )
    task["phaseCycleCapReached"] = True
    task["devStepCount"] = 13
    state.SHARED_BOARD["In Progress"] = [task]

    with patch("backend.services.sprint_service._run_developer_step") as developer:
        with pytest.raises(ValueError, match="phase cycle cap"):
            run_in_progress_step("brief", "http://localhost:11434", task_id="T-CAPPED")
    developer.assert_not_called()
    assert task["devStepCount"] == 13


def test_latched_card_first_recovery_runs_developer():
    initialize()
    reset_workflow_settings()
    save_workflow_settings(
        {
            "enableSplitOnStuck": False,
            "maxStuckSteps": 1,
            "maxPoRoundTrips": 1,
            "pauseSprintOnNeedsUser": False,
        }
    )
    _empty_board()
    task = init_new_task(
        {"id": "T-RECOVER", "title": "Recover", "description": "d", "status": "In Progress"}
    )
    task["phaseCycleCapReached"] = True
    task["phaseCycleCapAt"] = 13
    task["devStepCount"] = 13
    task["poRoundTrips"] = 1
    state.SHARED_BOARD["In Progress"] = [task]

    with patch("backend.services.sprint_service._run_developer_step") as developer:
        run_sprint_step("brief", "http://localhost:11434")
    developer.assert_called_once()
    assert get_task_lane("T-RECOVER") == "In Progress"
    assert task["latchedRecoveryAttempted"] is True
    assert task["phaseCycleCapReached"] is False
    assert task["devStepCount"] == 0


def test_second_latch_after_recovery_parks_to_needs_user_without_dev():
    initialize()
    reset_workflow_settings()
    save_workflow_settings(
        {
            "enableSplitOnStuck": False,
            "maxStuckSteps": 1,
            "maxPoRoundTrips": 1,
            "pauseSprintOnNeedsUser": False,
            "requireBacklogRefinement": False,
        }
    )
    _empty_board()
    task = init_new_task(
        {"id": "T-LATCH-LINT", "title": "Lint wall", "description": "d", "status": "In Progress"}
    )
    task["phaseCycleCapReached"] = True
    task["phaseCycleCapAt"] = 13
    task["devStepCount"] = 13
    task["poRoundTrips"] = 1
    task["latchedRecoveryAttempted"] = True
    task["lastCommandDiagnostics"] = [{"command": "flutter analyze", "ok": False}]
    state.SHARED_BOARD["In Progress"] = [task]

    with patch("backend.services.sprint_service._run_developer_step") as developer:
        run_sprint_step("brief", "http://localhost:11434")
    developer.assert_not_called()
    assert get_task_lane("T-LATCH-LINT") == "Needs User"
    assert task["devStepCount"] == 13


def test_recovered_latched_card_does_not_block_backlog_claim():
    initialize()
    reset_workflow_settings()
    save_workflow_settings(
        {
            "enableSplitOnStuck": False,
            "requireBacklogRefinement": False,
            "pauseSprintOnNeedsUser": False,
            "splitCardWhenAcOver": 20,
        }
    )
    _empty_board()
    latched = init_new_task(
        {"id": "T-LATCHED", "title": "Latched", "description": "d", "status": "In Progress"}
    )
    latched["phaseCycleCapReached"] = True
    latched["latchedRecoveryAttempted"] = True
    latched["devStepCount"] = 13
    ready = init_new_task(
        {
            "id": "T-READY",
            "title": "Ready work",
            "description": "Implement the feature",
            "status": "Backlog",
            "acceptanceCriteria": ["a", "b"],
            "scope": "One screen",
            "testPlan": "Run unit tests",
            "workType": "implementation",
            "requiresDev": True,
            "requiresQa": True,
        }
    )
    state.SHARED_BOARD["In Progress"] = [latched]
    state.SHARED_BOARD["Backlog"] = [ready]

    from backend.services.sprint_service import has_sprint_work

    assert has_sprint_work() is True

    with patch("backend.services.sprint_service._run_developer_step") as developer:
        with patch("backend.services.sprint_service._recover_latched_dev_card") as recover:
            run_sprint_step("brief", "http://localhost:11434")
    recover.assert_not_called()
    developer.assert_called_once()
    assert get_task_lane("T-READY") == "In Progress"
    assert get_task_lane("T-LATCHED") == "In Progress"


def test_only_recovered_latched_in_progress_is_sprint_work_until_parked():
    initialize()
    reset_workflow_settings()
    _empty_board()
    latched = init_new_task(
        {"id": "T-ONLY", "title": "Only latched", "description": "d", "status": "In Progress"}
    )
    latched["phaseCycleCapReached"] = True
    latched["latchedRecoveryAttempted"] = True
    state.SHARED_BOARD["In Progress"] = [latched]

    from backend.services.sprint_service import has_sprint_work

    assert has_sprint_work() is True


def test_auto_sprint_ui_pauses_on_retry_watchdog():
    from pathlib import Path

    hook = (
        Path(__file__).resolve().parents[1]
        / "frontend/src/hooks/useAppState.ts"
    ).read_text(encoding="utf-8")
    assert "retry_watchdog" in hook
    assert "setAutoSprintPaused(true)" in hook


def test_manual_dev_move_splits_oversized_ready_card_and_retires_parent():
    initialize()
    reset_workflow_settings()
    save_workflow_settings({"splitCardWhenAcOver": 3})
    _empty_board()
    task = init_new_task(
        {
            "id": "T-LARGE",
            "title": "Focused delivery",
            "description": "Deliver the requested behavior",
            "status": "Backlog",
            "acceptanceCriteria": ["a", "b", "c", "d"],
            "scope": "One bounded component",
            "testPlan": "Run focused unit tests",
            "workType": "implementation",
            "requiresDev": True,
            "requiresQa": True,
        }
    )
    state.SHARED_BOARD["Backlog"] = [task]

    result = move_board_stage("T-LARGE", "In Progress")
    assert "split by acceptance criteria" in result
    parent = next(item for item in state.SHARED_BOARD["Done"] if item["id"] == "T-LARGE")
    assert parent["splitSuperseded"] is True
    assert parent["requiresDev"] is False
    assert parent["requiresQa"] is False
    children = state.SHARED_BOARD["Backlog"]
    assert len(children) == 2
    assert all(len(child["acceptanceCriteria"]) <= 3 for child in children)


def test_manual_dev_move_routes_missing_spec_to_needs_po():
    initialize()
    _empty_board()
    task = init_new_task(
        {
            "id": "T-NOT-READY",
            "title": "Not ready",
            "description": "Some behavior",
            "status": "Backlog",
            "acceptanceCriteria": ["a"],
            "workType": "implementation",
            "requiresDev": True,
        }
    )
    state.SHARED_BOARD["Backlog"] = [task]

    move_board_stage("T-NOT-READY", "In Progress")
    assert get_task_lane("T-NOT-READY") == "Needs PO"
