"""Needs User guard tests for update_board and dev heuristics."""

from backend.bootstrap import initialize
from backend.services.needs_user_guard import dev_explicit_needs_user, is_clarification_shaped


def test_clarification_not_explicit_needs_user():
    msg = "Please clarify requirements for the login screen"
    assert is_clarification_shaped(msg)
    assert not dev_explicit_needs_user(msg)


def test_update_board_needs_user_requires_question():
    initialize()
    from backend import state
    from backend.agents.registry import _guarded_update_board
    from backend.agents.task_context import init_new_task

    task = init_new_task({"id": "T-GUARD", "title": "T", "description": "D", "status": "In Progress"})
    state.SHARED_BOARD = {
        "Backlog": [],
        "In Progress": [task],
        "Needs PO": [],
        "Needs User": [],
        "QA": [],
        "Done": [],
    }
    state.ACTIVE_SPRINT_AGENT = "Developer"
    result = _guarded_update_board("T-GUARD", "Needs User")
    assert "Error" in result
    assert "user_question" in result.lower() or "userQuestion" in result


def test_update_board_clarification_routes_to_po():
    initialize()
    from backend import state
    from backend.agents.registry import _guarded_update_board
    from backend.agents.task_context import init_new_task, get_task_lane

    task = init_new_task({"id": "T-PO", "title": "T", "description": "D", "status": "In Progress"})
    state.SHARED_BOARD = {
        "Backlog": [],
        "In Progress": [task],
        "Needs PO": [],
        "Needs User": [],
        "QA": [],
        "Done": [],
    }
    state.ACTIVE_SPRINT_AGENT = "Developer"
    result = _guarded_update_board(
        "T-PO",
        "Needs User",
        user_question="Please clarify requirements for the dashboard layout",
    )
    assert "Needs PO" in result or get_task_lane("T-PO") == "Needs PO"


def test_bulk_escalate_api():
    initialize()
    from backend import state
    from backend.agents.task_context import init_new_task
    from backend.main import app
    from fastapi.testclient import TestClient

    t1 = init_new_task({"id": "T-B1", "title": "B1", "description": "D", "status": "Needs User"})
    t2 = init_new_task({"id": "T-B2", "title": "B2", "description": "D", "status": "Needs User"})
    state.SHARED_BOARD = {
        "Backlog": [],
        "In Progress": [],
        "Needs PO": [],
        "Needs User": [t1, t2],
        "QA": [],
        "Done": [],
    }
    client = TestClient(app)
    resp = client.post("/api/board/escalate-needs-user-to-po")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data.get("movedTaskIds") or []) == 2
    assert len(data["board"].get("Needs User") or []) == 0
    assert len(data["board"].get("Needs PO") or []) == 2
