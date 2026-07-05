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
    assert "/api/tasks/{task_id}/transcript" in paths


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


def test_clear_task_transcript_numeric_id():
    """PO JSON may emit numeric ids; API paths always use strings."""
    from backend import state
    from backend.agents.task_context import clear_task_transcript, init_new_task, record_task_transcript

    initialize()
    task = init_new_task({"id": 1, "title": "Numeric", "description": "d"})
    state.SHARED_BOARD.setdefault("Backlog", []).append(task)
    record_task_transcript("1", "assistant", "hello")
    assert clear_task_transcript("1") is True
    assert task["transcript"] == []
    assert task["id"] == "1"


def test_clear_task_transcript_api_numeric_id():
    from backend import state
    from backend.agents.task_context import init_new_task, record_task_transcript

    initialize()
    client = TestClient(app)
    task = init_new_task({"id": 1, "title": "Numeric", "description": "d"})
    state.SHARED_BOARD.setdefault("Backlog", []).append(task)
    record_task_transcript("1", "tool", "noise")
    response = client.delete("/api/tasks/1/transcript")
    assert response.status_code == 200
    assert task["transcript"] == []


def test_allhands_db_path_outside_repo():
    from pathlib import Path

    from backend.config import ALLHANDS_HOME, DB_PATH, ROOT_DIR

    assert Path(DB_PATH).parent == ALLHANDS_HOME
    assert Path(DB_PATH).parent != ROOT_DIR


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


def test_normalize_acceptance_criteria_objects():
    from backend.agents.task_context import init_new_task, normalize_acceptance_criteria

    items = normalize_acceptance_criteria(
        [{"description": "User can log in", "status": "pending"}, "Plain string"]
    )
    assert items == ["User can log in", "Plain string"]
    task = init_new_task(
        {
            "id": "T-AC",
            "title": "Test",
            "description": {"description": "Nested desc"},
            "acceptanceCriteria": [{"description": "AC one", "status": "open"}],
        }
    )
    assert task["description"] == "Nested desc"
    assert task["acceptanceCriteria"] == ["AC one"]


def test_parse_git_status():
    from backend.services.git_service import parse_git_status

    stdout = "## main\n M lib/main.dart\n?? pubspec.yaml\n"
    parsed = parse_git_status(stdout)
    assert parsed["branch"] == "main"
    assert parsed["clean"] is False
    assert len(parsed["entries"]) == 2
    assert parsed["entries"][0]["path"] == "lib/main.dart"


def test_terminal_api_response_shape():
    initialize()
    client = TestClient(app)

    def fake_run(command):
        return {"success": True, "stdout": "hello", "stderr": "", "returncode": 0}

    import backend.api.terminal as terminal_mod

    terminal_mod.run_command = fake_run
    response = client.post("/api/terminal/run", json={"command": "echo hi"})
    assert response.status_code == 200
    data = response.json()
    assert "output" in data
    assert "exitCode" in data
    assert data["exitCode"] == 0


def test_tool_alias_resolution():
    from backend import state
    from backend.services.tool_aliases import resolve_tool_call, save_alias

    initialize()
    save_alias("flutter_analyze", "run_command", {"command": "flutter analyze"})
    name, args, resolved = resolve_tool_call("flutter_analyze", {})
    assert resolved is True
    assert name == "run_command"
    assert args["command"] == "flutter analyze"


def test_move_board_stage_noop_same_lane():
    from backend import state
    from backend.agents.task_context import init_new_task
    from backend.services.board_service import move_board_stage

    initialize()
    task = init_new_task({"id": "T-NOOP", "title": "Noop", "description": "d"})
    state.SHARED_BOARD.setdefault("QA", []).append(task)
    before_decisions = len(task["decisions"])
    result = move_board_stage("T-NOOP", "QA")
    assert "already in" in result
    assert len(task["decisions"]) == before_decisions
    assert "T-NOOP" in [t["id"] for t in state.SHARED_BOARD.get("QA", [])]


def test_stuck_loop_escalates_to_needs_po():
    from backend import state
    from backend.agents.task_context import init_new_task
    from backend.services.sprint_service import _check_stuck_and_escalate

    initialize()
    task = init_new_task({"id": "T-STUCK", "title": "Stuck", "description": "d"})
    task["stuckLoops"] = 2
    state.SHARED_BOARD.setdefault("QA", []).append(task)
    _check_stuck_and_escalate("T-STUCK", "QA")
    assert "T-STUCK" in [t["id"] for t in state.SHARED_BOARD.get("Needs PO", [])]


def test_assign_unique_task_id_avoids_existing_board_ids():
    from backend import state
    from backend.agents.task_context import all_task_ids, assign_unique_task_id, init_new_task

    initialize()
    task = init_new_task({"id": "T-EXIST", "title": "Existing", "description": "d"})
    state.SHARED_BOARD.setdefault("Backlog", []).append(task)
    taken_before = all_task_ids()
    new_task = {"id": 1, "title": "New"}
    new_id = assign_unique_task_id(new_task, preserve_po_ref=True, existing_ids=set(taken_before))
    assert new_id not in taken_before
    assert new_id.startswith("TASK-")
    assert new_task["poRefId"] == "1"


def test_append_tasks_remaps_blocked_by():
    from backend import state
    from backend.services.sprint_service import _append_tasks

    initialize()
    state.SHARED_BOARD = {"Backlog": [], "Pending Approval": [], "In Progress": [], "Needs PO": [], "Needs User": [], "QA": [], "Done": []}
    count = _append_tasks(
        [
            {"id": 1, "title": "First", "description": "a", "acceptanceCriteria": ["a"]},
            {"id": 2, "title": "Second", "description": "b", "acceptanceCriteria": ["b"], "blockedBy": ["1"]},
        ]
    )
    assert count == 2
    backlog = state.SHARED_BOARD.get("Backlog", []) + state.SHARED_BOARD.get("Pending Approval", [])
    by_title = {t["title"]: t for t in backlog}
    assert by_title["Second"]["blockedBy"] == [by_title["First"]["id"]]
    assert by_title["First"]["id"].startswith("TASK-")
    assert by_title["First"]["poRefId"] == "1"


def test_dedupe_board_tasks_removes_cross_lane_duplicates():
    from backend import state
    from backend.agents.task_context import dedupe_board_tasks, init_new_task

    initialize()
    dup_id = "T-DUP"
    task_a = init_new_task({"id": dup_id, "title": "Dup A", "description": "d", "status": "Backlog"})
    task_b = init_new_task({"id": dup_id, "title": "Dup B", "description": "d", "status": "In Progress"})
    state.SHARED_BOARD = {
        "Backlog": [task_a],
        "In Progress": [task_b],
        "Needs PO": [],
        "Needs User": [],
        "QA": [],
        "Done": [],
    }
    removed = dedupe_board_tasks()
    assert removed == 1
    all_ids = [t["id"] for lane in state.SHARED_BOARD.values() for t in lane]
    assert all_ids.count(dup_id) == 1
    assert dup_id in [t["id"] for t in state.SHARED_BOARD.get("Backlog", [])]


def test_move_board_stage_removes_all_duplicate_copies():
    from backend import state
    from backend.agents.task_context import init_new_task
    from backend.services.board_service import move_board_stage

    initialize()
    dup_id = "T-MOVE-DUP"
    task_a = init_new_task({"id": dup_id, "title": "Dup", "description": "d"})
    task_b = init_new_task({"id": dup_id, "title": "Dup copy", "description": "d"})
    state.SHARED_BOARD = {
        "Backlog": [task_a],
        "QA": [task_b],
        "In Progress": [],
        "Needs PO": [],
        "Needs User": [],
        "Done": [],
    }
    move_board_stage(dup_id, "In Progress")
    all_ids = [t["id"] for lane in state.SHARED_BOARD.values() for t in lane]
    assert all_ids.count(dup_id) == 1
    assert dup_id in [t["id"] for t in state.SHARED_BOARD.get("In Progress", [])]
    assert dup_id not in [t["id"] for t in state.SHARED_BOARD.get("Backlog", [])]
    assert dup_id not in [t["id"] for t in state.SHARED_BOARD.get("QA", [])]
