"""Run In Progress sprint step — dev-only, skips Needs PO."""

from unittest.mock import patch

from backend import state
from backend.agents.task_context import init_new_task
from backend.bootstrap import initialize
from backend.services.sprint_service import run_in_progress_step, run_sprint_step


def test_run_in_progress_step_runs_dev_ignores_needs_po():
    initialize()
    state.SHARED_BOARD.clear()
    for lane in ("Backlog", "In Progress", "Needs User", "Needs PO", "QA", "Done", "Refinement", "Code Review"):
        state.SHARED_BOARD[lane] = []

    needs_po = init_new_task({"id": "T-NPO", "title": "PO question", "description": "Clarify", "status": "Needs PO"})
    in_progress = init_new_task({"id": "T-IP", "title": "Active dev task", "description": "Keep working", "status": "In Progress"})
    state.SHARED_BOARD["Needs PO"] = [needs_po]
    state.SHARED_BOARD["In Progress"] = [in_progress]

    dev_called = {"count": 0, "task_id": None}

    def fake_dev_step(task, *_args, **_kwargs):
        dev_called["count"] += 1
        dev_called["task_id"] = task.get("id")
        return "dev step ok"

    with patch("backend.services.sprint_service._run_developer_step", side_effect=fake_dev_step):
        run_in_progress_step("brief", "http://localhost:11434")

    assert dev_called["count"] == 1
    assert dev_called["task_id"] == "T-IP"


def test_run_in_progress_step_with_task_id():
    initialize()
    state.SHARED_BOARD.clear()
    for lane in ("Backlog", "In Progress", "Needs User", "Needs PO", "QA", "Done", "Refinement", "Code Review"):
        state.SHARED_BOARD[lane] = []

    first = init_new_task({"id": "T-1", "title": "First", "description": "A", "status": "In Progress", "priority": 1})
    second = init_new_task({"id": "T-2", "title": "Second", "description": "B", "status": "In Progress", "priority": 2})
    state.SHARED_BOARD["In Progress"] = [first, second]

    dev_called = {"task_id": None}

    def fake_dev_step(task, *_args, **_kwargs):
        dev_called["task_id"] = task.get("id")
        return "ok"

    with patch("backend.services.sprint_service._run_developer_step", side_effect=fake_dev_step):
        run_in_progress_step("brief", "http://localhost:11434", task_id="T-2")

    assert dev_called["task_id"] == "T-2"


def test_normal_sprint_step_prefers_needs_po_over_in_progress():
    initialize()
    state.SHARED_BOARD.clear()
    for lane in ("Backlog", "In Progress", "Needs User", "Needs PO", "QA", "Done", "Refinement", "Code Review"):
        state.SHARED_BOARD[lane] = []

    needs_po = init_new_task({"id": "T-NPO", "title": "PO question", "description": "Clarify", "status": "Needs PO"})
    in_progress = init_new_task({"id": "T-IP", "title": "Active dev task", "description": "Keep working", "status": "In Progress"})
    state.SHARED_BOARD["Needs PO"] = [needs_po]
    state.SHARED_BOARD["In Progress"] = [in_progress]

    dev_called = {"count": 0}
    po_called = {"count": 0}

    def fake_dev_step(*_args, **_kwargs):
        dev_called["count"] += 1
        return "dev"

    def fake_po_step(*_args, **_kwargs):
        po_called["count"] += 1
        return "po"

    with patch("backend.services.sprint_service._run_developer_step", side_effect=fake_dev_step):
        with patch("backend.services.sprint_service._run_po_clarification", side_effect=fake_po_step):
            with patch("backend.services.sprint_service.get_workflow_settings") as mock_ws:
                mock_ws.return_value = {
                    "pauseSprintOnNeedsUser": False,
                    "requireBacklogRefinement": False,
                    "requireCodeReview": False,
                }
                run_sprint_step("brief", "http://localhost:11434")

    assert po_called["count"] == 1
    assert dev_called["count"] == 0


def test_run_in_progress_api_returns_409_when_empty():
    from fastapi.testclient import TestClient
    from backend.main import app

    initialize()
    state.SHARED_BOARD.clear()
    for lane in ("Backlog", "In Progress", "Needs User", "Needs PO", "QA", "Done", "Refinement", "Code Review"):
        state.SHARED_BOARD[lane] = []

    client = TestClient(app)
    res = client.post("/api/sprint/run-in-progress", json={"brief": "test", "ollama_url": "http://localhost:11434"})
    assert res.status_code == 409
