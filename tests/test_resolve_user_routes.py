"""Needs User resolve routing tests."""

from backend import state
from backend.agents.task_context import init_new_task
from backend.bootstrap import initialize
from backend.services.board_service import move_board_stage


def _setup_needs_user_task(task_id: str = "T-RESOLVE"):
    for lane in (
        "Backlog",
        "In Progress",
        "Needs User",
        "Needs PO",
        "Refinement",
        "QA",
        "Done",
        "Code Review",
        "Pending Approval",
    ):
        state.SHARED_BOARD.setdefault(lane, [])
    task = init_new_task(
        {
            "id": task_id,
            "title": "Blocked",
            "description": "Need input",
            "status": "Needs User",
            "needsUserReason": "Missing API key",
            "needsUserAction": "Provide key",
        }
    )
    state.SHARED_BOARD["Needs User"] = [task]
    return task


def test_resolve_user_to_refinement():
    initialize()
    from fastapi.testclient import TestClient
    from backend.main import app

    _setup_needs_user_task("T-REF")
    client = TestClient(app)
    resp = client.post(
        "/api/tasks/T-REF/resolve-user",
        json={"answer": "Use staging credentials", "target": "refinement"},
    )
    assert resp.status_code == 200
    board = resp.json()["board"]
    assert any(t["id"] == "T-REF" for t in board.get("Refinement", []))
    assert not any(t["id"] == "T-REF" for t in board.get("Needs User", []))


def test_resolve_user_to_po():
    initialize()
    from fastapi.testclient import TestClient
    from backend.main import app

    _setup_needs_user_task("T-PO")
    client = TestClient(app)
    resp = client.post(
        "/api/tasks/T-PO/resolve-user",
        json={"answer": "Clarify scope with PO", "target": "po"},
    )
    assert resp.status_code == 200
    board = resp.json()["board"]
    assert any(t["id"] == "T-PO" for t in board.get("Needs PO", []))
