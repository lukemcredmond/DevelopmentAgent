"""Board-wide duplicate card audit."""

from backend import state
from backend.agents.task_context import find_task_by_id, init_new_task
from backend.bootstrap import initialize
from backend.services.board_duplicate_audit import (
    apply_board_duplicate_audit,
    audit_board_duplicates,
)


def _board(*tasks_by_lane):
    state.SHARED_BOARD.clear()
    for lane in (
        "Features",
        "Backlog",
        "In Progress",
        "Needs PO",
        "Needs User",
        "Blocked",
        "QA",
        "Done",
    ):
        state.SHARED_BOARD[lane] = []
    for lane, tasks in tasks_by_lane:
        state.SHARED_BOARD[lane] = tasks


def test_duplicate_audit_same_title_in_needs_po():
    initialize()
    t1 = init_new_task(
        {
            "id": "T-KEEP",
            "title": "Handle export edge cases",
            "description": "Long spec " * 20,
            "acceptanceCriteria": ["AC1", "AC2"],
            "status": "Needs PO",
        }
    )
    t2 = init_new_task(
        {
            "id": "T-DUP",
            "title": "Handle export edge cases",
            "description": "short",
            "status": "Needs PO",
        }
    )
    _board(("Needs PO", [t1, t2]))
    report = audit_board_duplicates()
    assert report["duplicateClusterCount"] == 1
    assert report["clusters"][0]["suggestedKeepTaskId"] == "T-KEEP"


def test_apply_recommended_moves_duplicate_to_blocked():
    initialize()
    t1 = init_new_task(
        {
            "id": "T-KEEP",
            "title": "Implement JSON export",
            "description": "d " * 30,
            "acceptanceCriteria": ["a"],
            "status": "In Progress",
            "files": [{"path": "lib/json_export_import.dart"}],
        }
    )
    t2 = init_new_task(
        {
            "id": "T-DUP",
            "title": "Implement JSON export",
            "description": "dup",
            "status": "In Progress",
            "files": [{"path": "lib/json_export_import.dart"}],
        }
    )
    _board(("In Progress", [t1, t2]))
    result = apply_board_duplicate_audit(apply_recommended=True)
    assert "T-DUP" in result["blockedOrDone"]
    live = find_task_by_id("T-DUP")
    assert live is not None
    assert live.get("duplicateOfTaskId") == "T-KEEP"
    assert live.get("poAutoSkip") is True
