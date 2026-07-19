"""Done-lane gate: stop mid-step work and refuse chat/activate without allowDoneRetry."""

import json
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from backend import state
from backend.agents.registry import agent_dev
from backend.agents.task_context import (
    init_new_task,
    is_task_done,
    set_active_sprint_context,
)
from backend.bootstrap import initialize
from backend.main import app
from backend.services.prompt_retry import retry_agent_step


class _FakeFunction:
    def __init__(self, name: str, arguments: dict):
        self.name = name
        self.arguments = json.dumps(arguments)


class _FakeToolCall:
    def __init__(self, name: str, arguments: dict):
        self.function = _FakeFunction(name, arguments)


class _FakeMessage:
    def __init__(self, *, content=None, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls


class _FakeResponse:
    def __init__(self, message):
        self.message = message


def _empty_board():
    state.SHARED_BOARD.clear()
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


def test_set_active_sprint_context_refuses_done():
    initialize()
    _empty_board()
    task = init_new_task({"id": "T-DONE", "title": "Done card", "description": "d"})
    state.SHARED_BOARD["Done"] = [task]
    assert is_task_done("T-DONE")
    with pytest.raises(ValueError, match="already Done"):
        set_active_sprint_context("T-DONE", "Developer")
    set_active_sprint_context("T-DONE", "Developer", allow_done_retry=True)
    assert state.ACTIVE_SPRINT_TASK_ID == "T-DONE"
    assert state.ALLOW_DONE_RETRY is True


def test_execute_step_stops_when_task_moves_to_done():
    initialize()
    _empty_board()
    task = init_new_task(
        {"id": "T-MID", "title": "Mid Done", "description": "d", "status": "In Progress"}
    )
    state.SHARED_BOARD["In Progress"] = [task]
    tools_seen: list[str] = []

    chat_calls = {"count": 0}

    def fake_chat(messages, **kwargs):
        chat_calls["count"] += 1
        if chat_calls["count"] == 1:
            return _FakeResponse(
                _FakeMessage(
                    tool_calls=[
                        _FakeToolCall(
                            "update_board",
                            {"task_id": "T-MID", "status": "Done"},
                        )
                    ]
                )
            )
        return _FakeResponse(
            _FakeMessage(
                tool_calls=[
                    _FakeToolCall(
                        "write_file",
                        {"path": "x.txt", "content": "should not run"},
                    )
                ]
            )
        )

    def fake_exec(agent_id, tool_name, arguments, **kwargs):
        tools_seen.append(tool_name)
        if tool_name == "update_board":
            # Simulate move to Done mid-step
            t = state.SHARED_BOARD["In Progress"].pop(0)
            state.SHARED_BOARD["Done"].append(t)
            return type(
                "R",
                (),
                {
                    "tool_name": tool_name,
                    "tool_output": "Moved to Done",
                    "success": True,
                    "pending_approval": False,
                },
            )()
        return type(
            "R",
            (),
            {
                "tool_name": tool_name,
                "tool_output": "unexpected",
                "success": True,
                "pending_approval": False,
            },
        )()

    set_active_sprint_context("T-MID", "Developer")
    with patch.object(agent_dev, "_chat", side_effect=fake_chat):
        with patch("backend.agents.scrum_agent.execute_tool", side_effect=fake_exec):
            with patch("backend.services.llm_context.prune_messages_if_needed", lambda m: m):
                result = agent_dev.execute_step("Finish task", max_iterations=4)

    assert result == "Stopped: task already Done"
    assert tools_seen == ["update_board"]
    assert chat_calls["count"] == 1


def test_chat_rejects_done_task_id():
    initialize()
    _empty_board()
    task = init_new_task({"id": "T-CHAT", "title": "Chat Done", "description": "d"})
    state.SHARED_BOARD["Done"] = [task]
    client = TestClient(app)
    resp = client.post(
        "/api/chat",
        json={"message": "hello", "agent": "dev", "taskId": "T-CHAT"},
    )
    assert resp.status_code == 400
    assert "Done" in resp.json()["detail"]


def test_retry_rejects_done_without_allow():
    initialize()
    _empty_board()
    task = init_new_task({"id": "T-RETRY", "title": "Retry Done", "description": "d"})
    state.SHARED_BOARD["Done"] = [task]
    result = retry_agent_step("T-RETRY", "dev", "http://localhost:11434")
    assert result["ok"] is False
    assert "Done" in result["error"]
