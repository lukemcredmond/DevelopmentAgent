"""Sprint should not dev_recovery churn on visit-capped In Progress cards."""

from backend import state
from backend.agents.task_context import init_new_task
from backend.bootstrap import initialize
from backend.services.sprint_service import (
    _dev_step_blocked_latched,
    _in_progress_pending_recovery,
    _select_sprint_step_handler,
)


def _board_with_capped_card():
    initialize()
    capped = init_new_task(
        {
            "id": "T-CAP",
            "title": "Meal library shell",
            "description": "d",
            "status": "In Progress",
            "acceptanceCriteria": ["ok"],
        }
    )
    capped["phaseCycleCapReached"] = True
    capped["consecutiveNoWriteStall"] = 0
    state.SHARED_BOARD = {
        "Backlog": [],
        "In Progress": [capped],
        "Needs PO": [],
        "Needs User": [],
        "Needs Dev": [],
        "QA": [],
        "Done": [],
    }
    return capped


def test_phase_cap_excluded_from_pending_recovery():
    _board_with_capped_card()
    pending = _in_progress_pending_recovery()
    assert pending == []


def test_phase_cap_blocks_dev_without_reset():
    capped = _board_with_capped_card()
    assert _dev_step_blocked_latched(capped) is True


def test_sprint_handler_not_dev_recovery_for_capped_only_board():
    _board_with_capped_card()
    handler, active = _select_sprint_step_handler()
    assert handler != "dev_recovery"
    assert handler != "dev" or active is None
