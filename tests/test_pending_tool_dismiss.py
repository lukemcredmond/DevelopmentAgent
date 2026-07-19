"""Dismiss unknown-tool requests and stop-sprint suppression."""

from backend import state
from backend.agents.registry import agent_dev, configure_agent_tools
from backend.bootstrap import initialize
from backend.services.tool_aliases import (
    _DISMISSED_ALIASES,
    dismiss_all_pending_tools,
    dismiss_pending_tool,
    list_pending_tools,
    queue_pending_tool,
)


def _clear_pending():
    # Persist-dismiss anything left in SQLite from prior runs, then reset session state.
    dismiss_all_pending_tools(cancel_sprint=False)
    state.PENDING_TOOL_REQUESTS.clear()
    _DISMISSED_ALIASES.clear()
    state.SPRINT_CANCEL = False


def test_dismiss_pending_removes_from_list():
    initialize()
    _clear_pending()
    req = queue_pending_tool("flutter_analyze_magic", {"x": 1}, agent_role="Developer")
    assert req["status"] == "pending"
    assert len(list_pending_tools()) == 1
    dismissed = dismiss_pending_tool(req["id"])
    assert dismissed is not None
    assert dismissed["status"] == "dismissed"
    assert list_pending_tools() == []


def test_queue_suppressed_when_sprint_cancel():
    initialize()
    _clear_pending()
    state.SPRINT_CANCEL = True
    before = len(list_pending_tools())
    req = queue_pending_tool("invent_tool_xyz", {}, agent_role="Developer")
    assert req["status"] == "suppressed"
    assert len(list_pending_tools()) == before
    state.SPRINT_CANCEL = False


def test_queue_suppressed_after_alias_dismissed():
    initialize()
    _clear_pending()
    req = queue_pending_tool("weird_invent", {}, agent_role="Developer")
    dismiss_pending_tool(req["id"])
    assert list_pending_tools() == []
    again = queue_pending_tool("weird_invent", {"a": 1}, agent_role="Developer")
    assert again["status"] == "suppressed"
    assert list_pending_tools() == []


def test_dismiss_all_with_cancel_sprint():
    initialize()
    _clear_pending()
    queue_pending_tool("a_tool", {}, agent_role="Developer")
    queue_pending_tool("b_tool", {}, agent_role="Developer")
    assert len(list_pending_tools()) == 2
    count = dismiss_all_pending_tools(cancel_sprint=True)
    assert count == 2
    assert state.SPRINT_CANCEL is True
    assert list_pending_tools() == []
    # Further invents suppressed
    q = queue_pending_tool("c_tool", {})
    assert q["status"] == "suppressed"
    state.SPRINT_CANCEL = False


def test_dismiss_api_endpoints():
    initialize()
    _clear_pending()
    from fastapi.testclient import TestClient
    from backend.main import app

    client = TestClient(app)
    req = queue_pending_tool("api_invent", {}, agent_role="Developer")
    resp = client.post(f"/api/tools/pending/{req['id']}/dismiss")
    assert resp.status_code == 200
    assert resp.json()["pending"] == []

    queue_pending_tool("api_invent2", {})
    queue_pending_tool("api_invent3", {})
    resp2 = client.post("/api/tools/pending/dismiss-all", json={"cancelSprint": True})
    assert resp2.status_code == 200
    body = resp2.json()
    assert body["dismissed"] >= 2
    assert body["sprintCancel"] is True
    assert body["pending"] == []
    state.SPRINT_CANCEL = False


def test_true_invent_still_queues_when_not_cancelled():
    initialize()
    _clear_pending()
    state.REFINEMENT_MODE = False
    configure_agent_tools()
    before = len(list_pending_tools())
    result = agent_dev.registry.invoke("totally_fake_tool_zz", {})
    assert "not registered" in result.lower()
    assert len(list_pending_tools()) == before + 1
