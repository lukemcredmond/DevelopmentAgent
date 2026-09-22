"""Regression: auto sprint publishes starting progress before first step."""

from unittest.mock import patch

from backend.bootstrap import initialize
from backend import state


def _clear_board():
    for lane in list(state.SHARED_BOARD.keys()):
        state.SHARED_BOARD[lane] = []


def test_run_auto_sprint_publishes_starting_progress_first():
    initialize()
    _clear_board()

    from backend.services import sprint_service

    progress_calls = []

    def capture_progress(**kwargs):
        progress_calls.append(kwargs)

    with patch.object(sprint_service, "run_sprint_step", return_value=None):
        with patch.object(sprint_service, "publish_sprint_progress", side_effect=capture_progress):
            with patch(
                "backend.services.backlog_preflight.log_backlog_preflight_warnings"
            ) as preflight:
                summary = sprint_service.run_auto_sprint(
                    "brief",
                    "http://localhost:11434",
                    max_steps=5,
                )

    preflight.assert_called_once()
    assert summary.get("status") == "idle"
    assert progress_calls, "expected at least one sprint_progress publish"
    first = progress_calls[0]
    assert first.get("step") == 0 or first.get("status") == "starting"
    assert first.get("max_steps") == 5
