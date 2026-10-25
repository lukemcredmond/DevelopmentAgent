"""Auto sprint latch recovery batch."""

from unittest.mock import patch

from backend import state
from backend.agents.task_context import init_new_task
from backend.bootstrap import initialize
from backend.services.sprint_service import (
    _attempt_implementer_latch_recovery_batch,
    _implementer_auto_sprint_should_pause_on_latch,
)


def test_latch_recovery_batch_runs_on_skipped_cards():
    initialize()
    task = init_new_task(
        {
            "id": "T-LATCH",
            "title": "Stuck polish",
            "description": "d",
            "status": "In Progress",
            "acceptanceCriteria": ["ok"],
        }
    )
    task["phaseCycleCapReached"] = True
    task["poAutoSkip"] = True
    state.SHARED_BOARD = {
        "Backlog": [],
        "In Progress": [task],
        "Needs PO": [],
        "Needs User": [],
        "QA": [],
        "Done": [],
    }
    with patch(
        "backend.services.needs_user_guard.autonomous_recover_latched_task",
        return_value=False,
    ) as mock_recover:
        n = _attempt_implementer_latch_recovery_batch()
    assert n == 1
    mock_recover.assert_called_once()


def test_pause_on_latch_false_when_other_sprint_work():
    initialize()
    good = init_new_task(
        {
            "id": "T-OK",
            "title": "Runnable",
            "description": "d",
            "status": "In Progress",
            "acceptanceCriteria": ["ok"],
        }
    )
    latched = init_new_task(
        {
            "id": "T-BAD",
            "title": "Latched",
            "description": "d",
            "status": "In Progress",
            "acceptanceCriteria": ["ok"],
        }
    )
    latched["phaseCycleCapReached"] = True
    latched["poAutoSkip"] = True
    state.SHARED_BOARD = {
        "Backlog": [{"id": "T-B", "title": "backlog", "status": "Backlog"}],
        "In Progress": [good, latched],
        "Needs PO": [],
        "Needs User": [],
        "QA": [],
        "Done": [],
    }
    from backend.services.workflow_settings import save_workflow_settings

    save_workflow_settings(
        {
            "executionProfile": "implementer",
            "implementerStopOnLatch": True,
            "implementerStopOnLatchCount": 1,
        }
    )
    assert _implementer_auto_sprint_should_pause_on_latch() is False
