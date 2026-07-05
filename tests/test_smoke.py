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


def test_dev_registry_includes_read_file():
    from backend.agents.registry import agent_dev

    tool_names = {t["function"]["name"] for t in agent_dev.registry.get_ollama_tools()}
    assert "read_file" in tool_names
    assert "run_command" in tool_names


def test_transcript_trim_at_max():
    from backend import state
    from backend.agents.task_context import init_new_task, record_task_transcript
    from backend.config import MAX_TASK_TRANSCRIPT

    initialize()
    task = init_new_task({"id": "T-TR", "title": "Transcript", "description": "d"})
    state.SHARED_BOARD.setdefault("Backlog", []).append(task)
    for i in range(MAX_TASK_TRANSCRIPT + 10):
        record_task_transcript("T-TR", "tool", f"entry {i}")
    assert len(task["transcript"]) == MAX_TASK_TRANSCRIPT
    assert task["transcript"][0]["content"].startswith("entry 10")


def test_clear_task_transcript():
    from backend import state
    from backend.agents.task_context import clear_task_transcript, init_new_task, record_task_transcript

    initialize()
    task = init_new_task({"id": "T-CLR", "title": "Clear", "description": "d"})
    state.SHARED_BOARD.setdefault("Backlog", []).append(task)
    record_task_transcript("T-CLR", "assistant", "hello")
    assert clear_task_transcript("T-CLR") is True
    assert task["transcript"] == []


def test_run_agent_command_mock(monkeypatch):
    from backend.workspace.files import run_agent_command

    def fake_run(command):
        return {
            "success": True,
            "stdout": "No issues found!",
            "stderr": "",
            "returncode": 0,
        }

    monkeypatch.setattr(
        "backend.services.terminal_service.run_command",
        fake_run,
    )
    out = run_agent_command("flutter analyze")
    assert "success" in out
    assert "No issues found" in out


def test_move_task_while_sprint_unlocked():
    """Board move API should succeed without holding sprint lock during LLM."""
    from backend import state
    from backend.agents.task_context import init_new_task

    initialize()
    client = TestClient(app)
    task = init_new_task({"id": "T-MOVE", "title": "Move me", "description": "d"})
    state.SHARED_BOARD.setdefault("Backlog", []).append(task)
    state.ACTIVE_SPRINT_TASK_ID = "other"
    state.ACTIVE_SPRINT_AGENT = "Developer"
    response = client.post(
        "/api/tasks/move",
        json={"task_id": "T-MOVE", "target_lane": "In Progress", "fromLane": "Backlog", "toLane": "In Progress"},
    )
    assert response.status_code == 200
    assert "T-MOVE" in [t["id"] for t in state.SHARED_BOARD.get("In Progress", [])]
