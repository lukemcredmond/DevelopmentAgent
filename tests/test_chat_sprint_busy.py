"""Chat must not overlap a live auto-sprint agent run."""

from unittest.mock import patch

from fastapi.testclient import TestClient

from backend import state
from backend.agents.agent_run import AgentRunState, start_run
from backend.agents.registry import agent_dev
from backend.agents.task_context import init_new_task
from backend.bootstrap import initialize
from backend.main import app


def _empty_board() -> None:
    for lane in (
        "Backlog",
        "In Progress",
        "Needs User",
        "Needs PO",
        "QA",
        "Done",
        "Refinement",
        "Code Review",
    ):
        state.SHARED_BOARD[lane] = []


def test_chat_conflict_while_sprint_step_running_leaves_globals():
    initialize()
    _empty_board()
    task = init_new_task({"id": "T-CHAT-BUSY", "title": "Discuss me", "description": "d"})
    state.SHARED_BOARD["Needs User"] = [task]
    state.ACTIVE_SPRINT_TASK_ID = "T-SPRINT"
    state.ACTIVE_SPRINT_AGENT = "Developer"
    state.ALLOW_DONE_RETRY = False
    start_run("T-SPRINT", "Developer", max_iterations=8)
    client = TestClient(app)
    try:
        resp = client.post(
            "/api/chat",
            json={"message": "hello", "agent": "po", "taskId": "T-CHAT-BUSY"},
        )
        assert resp.status_code == 409
        detail = resp.json()["detail"]
        assert "T-SPRINT" in detail
        assert "Pause" in detail or "wait" in detail.lower()
        assert state.ACTIVE_SPRINT_TASK_ID == "T-SPRINT"
        assert state.ACTIVE_SPRINT_AGENT == "Developer"
        assert state.ACTIVE_AGENT_RUN is not None
        assert state.ACTIVE_AGENT_RUN.task_id == "T-SPRINT"
    finally:
        state.ACTIVE_AGENT_RUN = None
        state.ACTIVE_SPRINT_TASK_ID = None
        state.ACTIVE_SPRINT_AGENT = None


def test_chat_exception_returns_503_not_500_and_restores_sprint():
    initialize()
    _empty_board()
    task = init_new_task({"id": "T-CHAT-ERR", "title": "Discuss me", "description": "d"})
    state.SHARED_BOARD["In Progress"] = [task]
    state.ACTIVE_SPRINT_TASK_ID = "T-KEEP"
    state.ACTIVE_SPRINT_AGENT = "Developer"
    client = TestClient(app)
    with patch.object(agent_dev, "execute_step", side_effect=RuntimeError("ollama boom")):
        resp = client.post(
            "/api/chat",
            json={"message": "hello", "agent": "dev", "taskId": "T-CHAT-ERR"},
        )
    assert resp.status_code == 503
    assert resp.status_code != 500
    assert "RuntimeError" in resp.json()["detail"]
    assert "ollama boom" in resp.json()["detail"]
    assert state.ACTIVE_SPRINT_TASK_ID == "T-KEEP"
    assert state.ACTIVE_SPRINT_AGENT == "Developer"


def test_chat_success_restores_sprint_globals():
    initialize()
    _empty_board()
    task = init_new_task({"id": "T-CHAT-OK", "title": "Discuss me", "description": "d"})
    state.SHARED_BOARD["Needs PO"] = [task]
    state.ACTIVE_SPRINT_TASK_ID = "T-KEEP"
    state.ACTIVE_SPRINT_AGENT = "Developer"
    client = TestClient(app)
    with patch.object(agent_dev, "execute_step", return_value="ok from chat"):
        # PO agent is used for Needs PO discuss; patch po as well via registry
        from backend.agents.registry import agent_po

        with patch.object(agent_po, "execute_step", return_value="ok from chat"):
            resp = client.post(
                "/api/chat",
                json={"message": "hello", "agent": "po", "taskId": "T-CHAT-OK"},
            )
    assert resp.status_code == 200
    assert resp.json().get("response") == "ok from chat"
    assert state.ACTIVE_SPRINT_TASK_ID == "T-KEEP"
    assert state.ACTIVE_SPRINT_AGENT == "Developer"
