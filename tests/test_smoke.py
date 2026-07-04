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
