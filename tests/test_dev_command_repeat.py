"""Dev step repeated run_command detection."""

from backend.agents.task_context import init_new_task
from backend.services.sprint_service import _dev_step_repeated_command_no_progress


def test_repeated_command_no_progress_detects_duplicate_success():
    task = init_new_task({"id": "T-CMD", "title": "Clean", "description": "d", "status": "In Progress"})
    step_started = "2026-01-01 10:00:00"
    task["transcript"] = [
        {
            "timestamp": "2026-01-01 10:00:01",
            "toolName": "run_command",
            "toolSuccess": True,
            "toolArgs": {"command": "flutter clean"},
        },
        {
            "timestamp": "2026-01-01 10:00:02",
            "toolName": "run_command",
            "toolSuccess": True,
            "toolArgs": {"command": "  flutter   clean  "},
        },
    ]
    assert _dev_step_repeated_command_no_progress(task, "In Progress", step_started) is True
