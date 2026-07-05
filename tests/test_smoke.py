"""Smoke tests for API routes and core helpers."""

from fastapi.testclient import TestClient

from backend.bootstrap import initialize
from backend.main import app
from backend.services.workflow_settings import DEFAULT_WORKFLOW_SETTINGS, get_workflow_settings


def test_app_starts_and_serves_state():
    initialize()
    client = TestClient(app)
    response = client.get("/api/state")
    assert response.status_code == 200
    data = response.json()
    assert "projectId" in data
    assert "board" in data
    assert "workflowSettings" in data
    assert "briefChangelog" in data
    assert "notifications" in data


def test_file_tree_endpoint():
    initialize()
    client = TestClient(app)
    response = client.get("/api/files/tree")
    assert response.status_code == 200


def test_ollama_health_endpoint():
    initialize()
    client = TestClient(app)
    response = client.get("/api/ollama/health")
    assert response.status_code == 200
    assert "ok" in response.json()


def test_openapi_routes_registered():
    initialize()
    client = TestClient(app)
    paths = client.get("/openapi.json").json().get("paths", {})
    assert "/api/sprint/plan-and-run" in paths
    assert "/api/workflow/settings" in paths
    assert "/api/tasks/reorder" in paths


def test_workflow_settings_defaults():
    initialize()
    ws = get_workflow_settings()
    assert ws["requireBacklogApproval"] is False
    assert ws["requireCodeReview"] is False
    assert ws["maxSprintSteps"] == DEFAULT_WORKFLOW_SETTINGS["maxSprintSteps"]


def test_build_file_context_block():
    from backend.workspace.files import build_file_context_block

    assert build_file_context_block([]) == ""


def test_tool_registry_ollama_schema():
    from backend.agents.registry import agent_dev

    tools = agent_dev.registry.get_ollama_tools()
    assert len(tools) >= 1
    write_tool = next(t for t in tools if t["function"]["name"] == "write_file")
    assert write_tool["type"] == "function"
    assert "path" in write_tool["function"]["parameters"]["properties"]


def test_move_board_publishes_sse():
    from backend import state
    from backend.services.board_service import move_board_stage, publish_board_update
    from backend.agents.task_context import init_new_task

    initialize()
    received = []

    class FakeQueue:
        def put_nowait(self, item):
            received.append(item)

    state.EVENT_SUBSCRIBERS.append(FakeQueue())
    task = init_new_task({"id": "T-TEST", "title": "Test", "description": "d"})
    state.SHARED_BOARD.setdefault("Backlog", []).append(task)
    move_board_stage("T-TEST", "In Progress")
    assert any(e.get("type") == "board" for e in received)
    publish_board_update("T-TEST", "In Progress")


def test_has_sprint_work_idle():
    from backend import state
    from backend.services.sprint_service import has_sprint_work

    initialize()
    state.SHARED_BOARD = {
        "Backlog": [],
        "In Progress": [],
        "Needs PO": [],
        "Needs User": [],
        "QA": [],
        "Done": [{"id": "X", "title": "done"}],
    }
    assert has_sprint_work() is False


def test_po_clarification_without_json_stays_in_needs_po():
    from backend import state
    from backend.services.sprint_service import _apply_po_clarification_result
    from backend.agents.task_context import init_new_task

    initialize()
    task = init_new_task({"id": "T-PO", "title": "Clarify me", "description": "vague"})
    state.SHARED_BOARD["Needs PO"] = [task]
    assert _apply_po_clarification_result(task, "no json here") is False
    assert task["id"] in [t["id"] for t in state.SHARED_BOARD["Needs PO"]]


def test_workflow_max_po_round_trips_default():
    initialize()
    ws = get_workflow_settings()
    assert ws.get("maxPoRoundTrips") == 3


def test_assign_skills_route_registered():
    initialize()
    client = TestClient(app)
    paths = client.get("/openapi.json").json().get("paths", {})
    assert "/api/assign-skills" in paths
