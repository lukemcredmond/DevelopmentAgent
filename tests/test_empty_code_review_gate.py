"""Gate In Progress → Code Review/QA on real file writes (not reads-only)."""

from __future__ import annotations

from backend.bootstrap import initialize
from backend.agents.task_context import get_task_lane, init_new_task
from backend.services.workflow_settings import reset_workflow_settings, save_workflow_settings


def _board(**lanes):
    base = {
        "Features": [],
        "Backlog": [],
        "Refinement": [],
        "In Progress": [],
        "Needs PO": [],
        "Needs User": [],
        "Code Review": [],
        "QA": [],
        "Done": [],
    }
    base.update(lanes)
    return base


def test_task_has_write_files_ignores_reads():
    from backend.services.sprint_service import _task_has_write_files, _task_has_work_files

    task = {
        "files": [
            {"path": "lib/a.dart", "action": "read"},
            {"path": "lib/b.dart", "action": "context"},
        ]
    }
    assert _task_has_work_files(task) is True
    assert _task_has_write_files(task) is False

    task["files"].append({"path": "lib/c.dart", "action": "written"})
    assert _task_has_write_files(task) is True


def test_dev_gate_blocks_advance_without_writes():
    from backend.services.sprint_service import dev_gate_blocks_advance

    initialize()
    reset_workflow_settings()
    task = init_new_task({"id": "T-NW", "title": "No write", "description": "d"})
    task["files"] = [{"path": "lib/a.dart", "action": "read"}]
    blocked, reason = dev_gate_blocks_advance(task)
    assert blocked is True
    assert "no files written" in reason.lower()


def test_update_board_to_code_review_blocked_without_writes():
    from backend import state
    from backend.agents.registry import _guarded_update_board

    initialize()
    reset_workflow_settings()
    save_workflow_settings({"requireCodeReview": True})
    task = init_new_task({"id": "T-CR-EMPTY", "title": "Shopping list", "description": "d"})
    task["files"] = [{"path": "lib/shop.dart", "action": "read"}]
    state.SHARED_BOARD = _board(**{"In Progress": [task]})
    state.ACTIVE_SPRINT_AGENT = "Developer"

    result = _guarded_update_board("T-CR-EMPTY", "Code Review")
    assert result.startswith("Error:")
    assert "no files written" in result.lower()
    assert get_task_lane("T-CR-EMPTY") == "In Progress"


def test_update_board_to_code_review_allowed_with_write():
    from backend import state
    from backend.agents.registry import _guarded_update_board

    initialize()
    reset_workflow_settings()
    save_workflow_settings({"requireCodeReview": True})
    task = init_new_task({"id": "T-CR-OK", "title": "Shopping list", "description": "d"})
    task["files"] = [{"path": "lib/shop.dart", "action": "written"}]
    state.SHARED_BOARD = _board(**{"In Progress": [task]})
    state.ACTIVE_SPRINT_AGENT = "Developer"

    result = _guarded_update_board("T-CR-OK", "Code Review")
    assert "Error" not in result
    assert get_task_lane("T-CR-OK") == "Code Review"


def test_audit_reverts_code_review_without_writes():
    from backend import state
    from backend.services.sprint_service import _audit_dev_files_written

    initialize()
    reset_workflow_settings()
    save_workflow_settings({"requireCodeReview": True})
    task = init_new_task({"id": "T-CR-REV", "title": "Empty CR", "description": "d"})
    task["files"] = [{"path": "lib/a.dart", "action": "read"}]
    state.SHARED_BOARD = _board(**{"Code Review": [task]})
    task["status"] = "Code Review"

    _audit_dev_files_written(task, "In Progress", "T-CR-REV")
    assert get_task_lane("T-CR-REV") == "In Progress"


def test_read_only_no_edits_when_files_are_reads_only():
    from backend import state
    from backend.services.sprint_service import _dev_step_read_only_no_edits

    initialize()
    task = init_new_task({"id": "T-RO2", "title": "Read only", "description": "d"})
    task["files"] = [{"path": "lib/a.dart", "action": "read"}]
    step_started = "2026-01-01 00:00:00"
    task["transcript"] = [
        {
            "role": "tool",
            "toolName": "read_file",
            "toolSuccess": True,
            "timestamp": "2026-01-01 00:00:01",
            "content": "read_file → lib/a.dart ✓",
        }
    ]
    state.SHARED_BOARD = _board(**{"In Progress": [task]})
    assert _dev_step_read_only_no_edits(task, "In Progress", step_started) is True
