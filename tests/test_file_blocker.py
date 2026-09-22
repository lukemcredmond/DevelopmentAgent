"""Shared-file blocker orchestration."""

from unittest.mock import patch

from backend import state
from backend.agents.task_context import get_task_lane, init_new_task
from backend.bootstrap import initialize
from backend.services.board_service import move_board_stage
from backend.services.file_blocker import (
    BLOCKED_BY_KIND_FILE_FIX,
    orchestrate_file_blocker,
    reconcile_file_blockers_from_board,
)
from backend.services.workflow_settings import save_workflow_settings


def _empty_board():
    initialize()
    state.SHARED_BOARD.clear()
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


def _settings():
    return {
        "enableBlockedLane": True,
        "enableFileBlockerOrchestration": True,
        "fileBlockerMinDependents": 2,
        "fileBlockerAutoCreateFixCard": True,
        "requireBacklogRefinement": False,
    }


def test_orchestrate_creates_fix_card_and_blocks_dependents():
    _empty_board()
    save_workflow_settings(_settings())
    dep1 = init_new_task(
        {
            "id": "LINT-A",
            "title": "Lint: lib/main.dart",
            "description": "d",
            "status": "In Progress",
            "lintSourceFile": "lib/main.dart",
            "lastCommandDiagnostics": [
                {"file": "lib/main.dart", "line": 1, "message": "error", "severity": "error"}
            ],
        }
    )
    dep2 = init_new_task(
        {
            "id": "LINT-B",
            "title": "Lint: lib/main.dart",
            "description": "d",
            "status": "Needs User",
            "lintSourceFile": "lib/main.dart",
            "userQuestion": "Which lint error?",
            "needsUserKind": "lint",
        }
    )
    state.SHARED_BOARD["In Progress"] = [dep1]
    state.SHARED_BOARD["Needs User"] = [dep2]

    with patch("backend.services.file_blocker.get_workflow_settings", return_value=_settings()):
        result = orchestrate_file_blocker(
            file_path="lib/main.dart",
            source_task_id="LINT-A",
            reason="test",
        )

    assert result["fixTaskId"]
    assert len(result["blocked"]) == 1
    assert get_task_lane("LINT-B") == "Blocked"
    assert dep2.get("blockedByKind") == BLOCKED_BY_KIND_FILE_FIX
    assert result["fixTaskId"] in (dep2.get("blockedBy") or [])
    assert dep2.get("userQuestion") is None


def test_blocked_lane_releases_when_fix_card_done():
    _empty_board()
    save_workflow_settings(_settings())
    fix = init_new_task(
        {
            "id": "FIX-1",
            "title": "Lint: lib/widget.dart",
            "description": "d",
            "status": "In Progress",
            "lintSourceFile": "lib/widget.dart",
            "fileFixFor": "lib/widget.dart",
        }
    )
    dep = init_new_task(
        {
            "id": "DEP-1",
            "title": "Lint: lib/widget.dart",
            "description": "d",
            "status": "Blocked",
            "lintSourceFile": "lib/widget.dart",
            "blockedBy": ["FIX-1"],
            "blockedByKind": BLOCKED_BY_KIND_FILE_FIX,
            "blockedReturnLane": "In Progress",
        }
    )
    state.SHARED_BOARD["In Progress"] = [fix]
    state.SHARED_BOARD["Blocked"] = [dep]

    move_board_stage("FIX-1", "Done")
    assert get_task_lane("DEP-1") == "In Progress"


def test_reconcile_groups_needs_user_lint_pile():
    _empty_board()
    save_workflow_settings(_settings())
    cards = []
    for i in range(3):
        t = init_new_task(
            {
                "id": f"LINT-{i}",
                "title": "Lint: lib/main.dart",
                "description": "d",
                "status": "Needs User",
                "lintSourceFile": "lib/main.dart",
                "userQuestion": "fix lint?",
            }
        )
        cards.append(t)
    state.SHARED_BOARD["Needs User"] = cards

    with patch("backend.services.file_blocker.get_workflow_settings", return_value=_settings()):
        result = reconcile_file_blockers_from_board()

    assert result["blocked"] >= 2
    blocked_count = len(state.SHARED_BOARD.get("Blocked") or [])
    assert blocked_count >= 2
