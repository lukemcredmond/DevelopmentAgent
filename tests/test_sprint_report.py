"""Sprint report lane-move rollups."""

from unittest.mock import patch

from backend import state
from backend.agents.task_context import get_task_lane, init_new_task
from backend.bootstrap import initialize
from backend.services.blocked_lane import sync_blocked_lane
from backend.services.board_service import move_board_stage
from backend.services.sprint_report import (
    begin_sprint_report,
    current_sprint_report,
    finalize_sprint_report,
    list_sprint_reports,
)
from backend.services.sprint_service import _build_sprint_summary


def _empty_board():
    initialize()
    state.CURRENT_SPRINT_REPORT = None
    from backend.services.sprint_report import _persist_reports

    _persist_reports([])
    for lane in (
        "Features",
        "Backlog",
        "Pending Approval",
        "Refinement",
        "Blocked",
        "In Progress",
        "Needs PO",
        "Needs User",
        "Code Review",
        "QA",
        "Done",
    ):
        state.SHARED_BOARD[lane] = []


def test_report_records_done_and_unblock():
    _empty_board()
    begin_sprint_report()
    done_card = init_new_task(
        {"id": "T-MOVE-DONE", "title": "Ship it", "description": "d", "status": "In Progress"}
    )
    blocked = init_new_task(
        {
            "id": "T-UNBLOCK",
            "title": "Waiting",
            "description": "p",
            "status": "Blocked",
            "blockedBy": [],
        }
    )
    state.SHARED_BOARD["In Progress"] = [done_card]
    state.SHARED_BOARD["Blocked"] = [blocked]

    move_board_stage("T-MOVE-DONE", "Done")
    assert get_task_lane("T-MOVE-DONE") == "Done"

    with patch(
        "backend.services.workflow_settings.get_workflow_settings",
        return_value={"enableBlockedLane": True, "requireBacklogRefinement": False},
    ), patch(
        "backend.services.blocked_lane.get_workflow_settings",
        return_value={"enableBlockedLane": True, "requireBacklogRefinement": False},
    ):
        sync_blocked_lane(persist=False)

    assert get_task_lane("T-UNBLOCK") == "Backlog"
    live = current_sprint_report()
    assert live is not None
    assert live["movedCount"] >= 2
    assert live["unblocked"] == 1
    assert live["byTransition"].get("In Progress → Done") == 1
    assert live["byTransition"].get("Blocked → Backlog") == 1

    finished = finalize_sprint_report(2, "completed")
    assert finished["status"] == "completed"
    assert finished["stepsRun"] == 2
    assert current_sprint_report() is None
    history = list_sprint_reports()
    assert history
    assert history[0]["unblocked"] == 1


def test_build_sprint_summary_finalizes_open_report():
    _empty_board()
    begin_sprint_report()
    card = init_new_task(
        {"id": "T-SUM", "title": "Sum", "description": "d", "status": "In Progress"}
    )
    state.SHARED_BOARD["In Progress"] = [card]
    move_board_stage("T-SUM", "QA")
    _build_sprint_summary(1, "idle")
    assert current_sprint_report() is None
    assert list_sprint_reports()[0]["byTransition"].get("In Progress → QA") == 1


def test_state_payload_includes_sprint_reports():
    _empty_board()
    begin_sprint_report()
    from backend.api.helpers import build_state_response

    payload = build_state_response(include_files=False)
    assert "sprintReports" in payload
    assert payload["currentSprintReport"] is not None
    assert payload["currentSprintReport"]["status"] == "running"
