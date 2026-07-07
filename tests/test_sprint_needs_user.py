"""Sprint behavior when Needs User cards are present."""

from unittest.mock import patch

from backend import state
from backend.agents.task_context import init_new_task
from backend.bootstrap import initialize
from backend.services.sprint_service import run_sprint_step


def test_sprint_continues_in_progress_when_needs_user_present():
    initialize()
    state.SHARED_BOARD.clear()
    for lane in ("Backlog", "In Progress", "Needs User", "Needs PO", "QA", "Done", "Refinement", "Code Review"):
        state.SHARED_BOARD[lane] = []

    needs_user = init_new_task({"id": "T-NU", "title": "Blocked task", "description": "Needs clarification", "status": "Needs User"})
    in_progress = init_new_task({"id": "T-IP", "title": "Active dev task", "description": "Keep working", "status": "In Progress"})
    state.SHARED_BOARD["Needs User"] = [needs_user]
    state.SHARED_BOARD["In Progress"] = [in_progress]

    dev_called = {"count": 0}

    def fake_dev_step(*_args, **_kwargs):
        dev_called["count"] += 1
        return "dev step ok"

    with patch("backend.services.sprint_service._run_developer_step", side_effect=fake_dev_step):
        with patch("backend.services.sprint_service.get_workflow_settings") as mock_ws:
            mock_ws.return_value = {"pauseSprintOnNeedsUser": False, "requireBacklogRefinement": False, "requireCodeReview": False}
            run_sprint_step("brief", "http://localhost:11434")

    assert dev_called["count"] == 1


def test_sprint_pauses_on_needs_user_when_setting_enabled():
    initialize()
    state.SHARED_BOARD.clear()
    for lane in ("Backlog", "In Progress", "Needs User", "Needs PO", "QA", "Done", "Refinement", "Code Review"):
        state.SHARED_BOARD[lane] = []

    needs_user = init_new_task({"id": "T-NU", "title": "Blocked task", "description": "Needs clarification", "status": "Needs User"})
    in_progress = init_new_task({"id": "T-IP", "title": "Active dev task", "description": "Keep working", "status": "In Progress"})
    state.SHARED_BOARD["Needs User"] = [needs_user]
    state.SHARED_BOARD["In Progress"] = [in_progress]

    dev_called = {"count": 0}

    def fake_dev_step(*_args, **_kwargs):
        dev_called["count"] += 1
        return "dev step ok"

    with patch("backend.services.sprint_service._run_developer_step", side_effect=fake_dev_step):
        with patch("backend.services.sprint_service.get_workflow_settings") as mock_ws:
            mock_ws.return_value = {"pauseSprintOnNeedsUser": True, "requireBacklogRefinement": False, "requireCodeReview": False}
            run_sprint_step("brief", "http://localhost:11434")

    assert dev_called["count"] == 0


def test_apply_patch_escalates_after_two_failures():
    from backend.workspace import files

    initialize()
    state.STEP_FILE_READS.clear()
    state.STEP_PATCH_FAILURES.clear()
    state.STEP_FILE_READS["lib/main.dart"] = "void main() {}\n"

    msg1 = files.apply_workspace_patch("lib/main.dart", "missing text", "new")
    assert "old_text not found" in msg1
    assert state.STEP_PATCH_FAILURES.get("lib/main.dart") == 1

    msg2 = files.apply_workspace_patch("lib/main.dart", "still missing", "new")
    assert "Patch failed 2 times" in msg2
    assert "write_file" in msg2
