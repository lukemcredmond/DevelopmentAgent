"""Smoke tests for API routes and core helpers."""

from fastapi.testclient import TestClient

from backend.bootstrap import initialize
from backend.main import app
from backend.services.workflow_settings import DEFAULT_WORKFLOW_SETTINGS, get_workflow_settings, reset_workflow_settings


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
    assert "/api/tasks/{task_id}/split" in paths


def test_workflow_settings_defaults():
    initialize()
    reset_workflow_settings()
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
        "backend.services.command_result.run_command",
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


def test_is_tool_failure_detects_common_errors():
    from backend.agents.tool_outcomes import is_tool_failure

    assert is_tool_failure("write_file", "Error: Path escapes workspace: ../etc/passwd")
    assert is_tool_failure("write_file", "Error: physical write failed for 'x': denied")
    assert is_tool_failure("run_command", "[failed exit 1]\nstderr")
    assert is_tool_failure("run_test", "❌ QA Validation Failure: bad")
    assert not is_tool_failure("write_file", "Successfully saved file physically at: '/tmp/x'")


def test_run_command_findings_not_tool_failure():
    from backend.agents.tool_outcomes import (
        classify_run_command,
        is_run_command_failure,
        is_tool_failure,
        run_command_status_label,
    )

    analyze_output = (
        "[findings exit 1]\n"
        "Analyzing workspace...\n"
        "  error • Undefined name 'foo' • lib/main.dart:10:5 • undefined_identifier\n"
    )
    assert classify_run_command("flutter analyze", analyze_output) == "lint_findings"
    assert not is_run_command_failure(analyze_output)
    assert not is_tool_failure("run_command", analyze_output)
    assert run_command_status_label(analyze_output, True, "flutter analyze").startswith("Findings")

    blocked = "[failed exit 1]\n(no output)"
    assert classify_run_command("flutter analyze", blocked) == "execution_failed"
    assert is_run_command_failure(blocked)
    assert is_tool_failure("run_command", blocked)

    assert run_command_status_label("[success exit 0]\nAll good", True, "flutter analyze") == "OK"


def test_run_agent_command_findings_header(monkeypatch):
    from backend.workspace.files import run_agent_command

    def fake_run(command):
        return {
            "success": False,
            "stdout": "Analyzing workspace...\n  error • bad • lib/main.dart:1:1\n",
            "stderr": "",
            "returncode": 1,
        }

    monkeypatch.setattr(
        "backend.services.command_result.run_command",
        fake_run,
    )
    output = run_agent_command("flutter analyze")
    assert output.startswith("[findings exit 1]")
    assert "[failed exit" not in output.split("\n")[0]


def test_qa_playbook_analyze_findings_does_not_fail_playbook(monkeypatch):
    from backend.services import sprint_service

    calls = []

    def fake_run(command):
        calls.append(command)
        if "analyze" in command:
            return {
                "success": False,
                "stdout": "Analyzing…\n  warning • unused import • lib/main.dart:1:1\n",
                "stderr": "",
                "returncode": 1,
            }
        return {"success": True, "stdout": "All tests passed", "stderr": "", "returncode": 0}

    monkeypatch.setattr("backend.services.command_result.run_command", fake_run)
    monkeypatch.setattr(
        sprint_service,
        "derive_project_test_commands",
        lambda: ["flutter analyze", "flutter test"],
    )
    monkeypatch.setattr(sprint_service, "log_synthetic_tool_event", lambda *a, **k: None)

    playbook = sprint_service._run_qa_test_playbook("T-QA")
    assert playbook["passed"] is True
    assert playbook["results"][0]["outcome"] == "lint_findings"
    assert playbook["results"][1]["outcome"] == "ok"


def test_qa_playbook_test_failure_fails_playbook(monkeypatch):
    from backend.services import sprint_service

    def fake_run(command):
        if "test" in command:
            return {
                "success": False,
                "stdout": "Expected: true\n  Actual: false\n",
                "stderr": "",
                "returncode": 1,
            }
        return {"success": True, "stdout": "No issues found", "stderr": "", "returncode": 0}

    monkeypatch.setattr("backend.services.command_result.run_command", fake_run)
    monkeypatch.setattr(
        sprint_service,
        "derive_project_test_commands",
        lambda: ["flutter analyze", "flutter test"],
    )
    logged = []

    def capture_log(*args, **kwargs):
        logged.append(kwargs)

    monkeypatch.setattr(sprint_service, "log_synthetic_tool_event", capture_log)

    playbook = sprint_service._run_qa_test_playbook("T-QA2")
    assert playbook["passed"] is False
    assert playbook["results"][1]["outcome"] == "test_failed"
    assert logged[1]["success"] is True


def test_tool_history_same_second_distinct_events():
    from backend import state
    from backend.services.tool_execution_service import append_global_tool_event, get_tool_history

    initialize()
    state.TOOL_EXECUTION_LOG.clear()
    ts = "2026-07-05 12:00:00"
    append_global_tool_event(
        {
            "eventId": "evt-read",
            "runId": "run-1",
            "taskId": "T-1",
            "agent": "Developer",
            "toolName": "read_file",
            "toolArgs": {"path": "lib/main.dart"},
            "toolSuccess": True,
            "toolOutput": "contents",
            "durationMs": 10,
            "timestamp": ts,
            "source": "agent",
            "status": "completed",
        }
    )
    append_global_tool_event(
        {
            "eventId": "evt-grep",
            "runId": "run-1",
            "taskId": "T-1",
            "agent": "Developer",
            "toolName": "grep",
            "toolArgs": {"pattern": "foo"},
            "toolSuccess": True,
            "toolOutput": "matches",
            "durationMs": 5,
            "timestamp": ts,
            "source": "agent",
            "status": "completed",
        }
    )

    events = get_tool_history()
    tool_names = [e["toolName"] for e in events if e.get("taskId") == "T-1"]
    assert "read_file" in tool_names
    assert "grep" in tool_names


def test_user_inject_publishes_tool_start_and_end(monkeypatch):
    from backend import state
    from backend.agents.task_context import init_new_task
    from backend.services.tool_execution_service import record_user_tool_evidence

    initialize()
    state.TOOL_EXECUTION_LOG.clear()
    task = init_new_task({"id": "T-INJ2", "title": "Inject2", "description": "d"})
    state.SHARED_BOARD.setdefault("In Progress", []).append(task)

    published: list[tuple[str, dict]] = []

    def capture(event_type, payload):
        published.append((event_type, payload))

    monkeypatch.setattr("backend.services.tool_execution_service.publish_event", capture)

    record_user_tool_evidence(
        "T-INJ2",
        "run_command",
        {"command": "flutter analyze"},
        "  error • bad • lib/main.dart:1:1\n",
    )

    event_types = [item[0] for item in published]
    assert "tool_start" in event_types
    assert "tool_end" in event_types
    start_payload = next(item[1] for item in published if item[0] == "tool_start")
    end_payload = next(item[1] for item in published if item[0] == "tool_end")
    assert start_payload.get("eventId")
    assert start_payload["eventId"] == end_payload["eventId"]


def test_diagnostics_parser_flutter_dash_and_generic():
    from backend.services.diagnostics_parser import parse_command_diagnostics, summarize_diagnostics

    dash_output = "  error - Undefined name 'foo' - lib/app.dart:3:1\n"
    dash_findings = parse_command_diagnostics("dart analyze", dash_output)
    assert len(dash_findings) == 1
    assert dash_findings[0]["file"] == "lib/app.dart"
    assert dash_findings[0]["line"] == 3

    generic_output = "src/index.ts:10:5 error TS2304: Cannot find name 'bar'\n"
    generic_findings = parse_command_diagnostics("npm run lint", generic_output)
    assert any(f["file"] == "src/index.ts" for f in generic_findings)
    assert summarize_diagnostics(dash_findings).startswith("1 error")


def test_command_result_short_analyze_output_is_lint_findings(monkeypatch):
    from backend.services.command_result import format_command_result_for_agent, run_workspace_command

    def fake_run(command):
        return {
            "success": False,
            "stdout": "  error • bad • lib/main.dart:10:5\n",
            "stderr": "",
            "returncode": 1,
        }

    monkeypatch.setattr("backend.services.command_result.run_command", fake_run)
    result = run_workspace_command("flutter analyze")
    assert result.outcome == "lint_findings"
    assert len(result.diagnostics) == 1
    formatted = format_command_result_for_agent(result)
    assert formatted.startswith("[findings exit 1]")
    assert "## Problems" in formatted
    assert "lib/main.dart:10:5" in formatted


def test_derive_project_lint_command(tmp_path, monkeypatch):
    from backend import state
    from backend.workspace.files import derive_project_lint_command

    initialize()
    monkeypatch.setattr(state, "WORKSPACE_DIR", str(tmp_path))
    state.VIRTUAL_FILESYSTEM.clear()

    (tmp_path / "pubspec.yaml").write_text("name: demo\n", encoding="utf-8")
    state.VIRTUAL_FILESYSTEM["pubspec.yaml"] = "name: demo\n"
    assert derive_project_lint_command() == "flutter analyze"

    state.VIRTUAL_FILESYSTEM.clear()
    for name in list(tmp_path.iterdir()):
        name.unlink()
    (tmp_path / "package.json").write_text(
        '{"scripts":{"lint":"eslint .","test":"jest"}}',
        encoding="utf-8",
    )
    state.VIRTUAL_FILESYSTEM["package.json"] = '{"scripts":{"lint":"eslint ."}}'
    assert derive_project_lint_command() == "npm run lint"


def test_inject_tool_evidence_api():
    from backend import state
    from backend.agents.task_context import init_new_task
    from backend.services.tool_execution_service import get_tool_history

    initialize()
    state.TOOL_EXECUTION_LOG.clear()
    task = init_new_task({"id": "T-INJ", "title": "Inject", "description": "d"})
    task["qaEvidence"] = {"playbookRun": True, "commands": ["flutter analyze"], "passed": False}
    state.SHARED_BOARD = {
        "QA": [task],
        "Backlog": [],
        "In Progress": [],
        "Needs PO": [],
        "Needs User": [],
        "Done": [],
    }

    client = TestClient(app)
    resp = client.post(
        "/api/tasks/T-INJ/inject-tool-evidence",
        json={
            "toolName": "run_command",
            "toolArgs": {"command": "flutter analyze"},
            "toolOutput": "Analyzing project…\nwarning • unused import\n5 issues found",
            "note": "User pasted analyze output",
        },
    )
    assert resp.status_code == 200
    updated = state.SHARED_BOARD["QA"][0]
    assert updated["qaEvidence"]["userOverride"] is True
    assert updated["qaEvidence"]["passed"] is True
    assert any(e["taskId"] == "T-INJ" and e.get("source") == "user" for e in get_tool_history())


def test_resolve_workspace_path_variants():
    import os

    from backend import state
    from backend.workspace.files import resolve_workspace_path

    initialize()
    ws = os.path.realpath(state.WORKSPACE_DIR)
    sample = os.path.join(ws, "lib", "main.dart")
    os.makedirs(os.path.dirname(sample), exist_ok=True)
    with open(sample, "w", encoding="utf-8") as f:
        f.write("// test")

    assert resolve_workspace_path("lib/main.dart") == "lib/main.dart"
    assert resolve_workspace_path("./lib/main.dart") == "lib/main.dart"
    assert resolve_workspace_path("lib\\main.dart") == "lib/main.dart"
    assert resolve_workspace_path(sample) == "lib/main.dart"
    basename = os.path.basename(ws.rstrip(os.sep))
    assert resolve_workspace_path(f"{basename}/lib/main.dart") == "lib/main.dart"


def test_write_workspace_file_invalid_path_error_prefix():
    from backend.workspace.files import write_workspace_file

    initialize()
    result = write_workspace_file("../outside.dart", "content")
    assert result.startswith("Error:")


def test_record_tool_usage_failure_metadata():
    from backend import state
    from backend.agents.task_context import init_new_task
    from backend.services.tool_execution_service import execute_tool

    initialize()
    task = init_new_task({"id": "T-TOOL-FAIL", "title": "Tool fail", "description": "d"})
    state.SHARED_BOARD.setdefault("In Progress", []).append(task)
    before_logs = len(state.SYSTEM_LOGS)

    execute_tool(
        "dev",
        "read_file",
        {"path": "missing-tool-fail.txt"},
        task_id="T-TOOL-FAIL",
        source="agent",
        skip_approval=True,
        user_prompt="implement",
    )

    entry = task["transcript"][-1]
    assert entry["toolSuccess"] is False
    assert entry["toolName"] == "read_file"
    assert entry["toolArgs"]["path"] == "missing-tool-fail.txt"
    assert any("FAILED" in log["text"] for log in state.SYSTEM_LOGS[before_logs:])


def test_audit_dev_files_written_warning():
    from backend import state
    from backend.agents.task_context import init_new_task
    from backend.services.sprint_service import _audit_dev_files_written

    initialize()
    task = init_new_task({"id": "T-NO-FILES", "title": "No files", "description": "d"})
    state.SHARED_BOARD = {
        "In Progress": [],
        "QA": [task],
        "Backlog": [],
        "Needs PO": [],
        "Needs User": [],
        "Done": [],
    }
    task["status"] = "QA"
    before = len(state.SYSTEM_LOGS)
    _audit_dev_files_written(task, "In Progress", "T-NO-FILES")
    assert any(
        "no files recorded" in log["text"].lower()
        for log in state.SYSTEM_LOGS[before:]
    )


def test_tool_requires_approval_settings():
    from backend.services.tool_approval import tool_requires_approval
    from backend.services.workflow_settings import save_workflow_settings

    initialize()
    save_workflow_settings({"requireToolApproval": False})
    assert tool_requires_approval("write_file") is False
    save_workflow_settings(
        {
            "requireToolApproval": True,
            "toolApprovalTools": ["write_file", "run_command", "delete_file"],
        }
    )
    assert tool_requires_approval("write_file") is True
    assert tool_requires_approval("apply_patch") is True
    assert tool_requires_approval("read_file") is False


def test_workflow_settings_api_persists_tool_approval():
    from backend.services.workflow_settings import get_workflow_settings

    initialize()
    client = TestClient(app)
    response = client.post(
        "/api/workflow/settings",
        json={"requireToolApproval": True},
    )
    assert response.status_code == 200
    assert response.json()["workflowSettings"]["requireToolApproval"] is True
    assert get_workflow_settings()["requireToolApproval"] is True
    response = client.post(
        "/api/workflow/settings",
        json={"requireToolApproval": False},
    )
    assert response.json()["workflowSettings"]["requireToolApproval"] is False


def test_resolve_tool_approval_unblocks():
    import threading
    from datetime import datetime

    from backend import state
    from backend.services.tool_approval import PendingToolApproval, resolve_tool_approval

    initialize()
    approval = PendingToolApproval(
        id="appr-test",
        run_id="RUN1",
        task_id="T1",
        agent="Developer",
        tool_name="write_file",
        arguments={"path": "lib/a.dart", "content": "x"},
        timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    )
    state.PENDING_TOOL_APPROVALS.append(approval)
    done = threading.Event()

    def wait_for_it():
        approval.event.wait(timeout=2)
        done.set()

    threading.Thread(target=wait_for_it, daemon=True).start()
    assert resolve_tool_approval("appr-test", True) is True
    assert done.wait(timeout=2)
    assert approval.approved is True


def test_apply_workspace_patch_replaces_unique_snippet():
    from backend import state
    from backend.workspace.files import apply_workspace_patch, write_workspace_file

    initialize()
    state.ACTIVE_SPRINT_TASK_ID = None
    state.STEP_FILE_READS.clear()
    write_workspace_file("patch_test.txt", "hello world")
    result = apply_workspace_patch("patch_test.txt", "world", "there")
    assert "Successfully saved" in result
    assert state.VIRTUAL_FILESYSTEM["patch_test.txt"] == "hello there"


def test_apply_workspace_patch_fails_when_old_text_missing():
    from backend import state
    from backend.workspace.files import apply_workspace_patch, write_workspace_file

    initialize()
    state.ACTIVE_SPRINT_TASK_ID = None
    state.STEP_FILE_READS.clear()
    write_workspace_file("patch_miss.txt", "alpha beta")
    result = apply_workspace_patch("patch_miss.txt", "gamma", "delta")
    assert result.startswith("Error:")
    assert "read_file" in result


def test_apply_patch_requires_read_file_this_step():
    from backend import state
    from backend.workspace.files import apply_workspace_patch, write_workspace_file

    initialize()
    state.ACTIVE_SPRINT_TASK_ID = "T-PATCH-GATE"
    state.STEP_FILE_READS.clear()
    write_workspace_file("gate_test.txt", "line one\nline two")
    result = apply_workspace_patch("gate_test.txt", "line one", "line 1")
    assert "requires read_file" in result


def test_apply_patch_after_read_file_succeeds():
    from backend import state
    from backend.workspace.files import (
        apply_workspace_patch,
        read_workspace_file,
        record_step_file_read,
        write_workspace_file,
    )

    initialize()
    state.ACTIVE_SPRINT_TASK_ID = "T-PATCH-OK"
    state.STEP_FILE_READS.clear()
    write_workspace_file("read_patch.txt", "foo bar baz")
    content = read_workspace_file("read_patch.txt")
    record_step_file_read("read_patch.txt", content)
    result = apply_workspace_patch("read_patch.txt", "bar", "qux")
    assert "Successfully saved" in result
    assert state.VIRTUAL_FILESYSTEM["read_patch.txt"] == "foo qux baz"


def test_apply_patch_after_write_requires_reread():
    from backend import state
    from backend.workspace.files import (
        apply_workspace_patch,
        read_workspace_file,
        record_step_file_read,
        write_workspace_file,
    )

    initialize()
    state.ACTIVE_SPRINT_TASK_ID = "T-PATCH-RE"
    state.STEP_FILE_READS.clear()
    write_workspace_file("re_read.txt", "original")
    content = read_workspace_file("re_read.txt")
    record_step_file_read("re_read.txt", content)
    write_workspace_file("re_read.txt", "updated content")
    result = apply_workspace_patch("re_read.txt", "updated", "changed")
    assert "requires read_file" in result


def test_apply_patch_not_found_includes_excerpt():
    from backend import state
    from backend.workspace.files import (
        apply_workspace_patch,
        record_step_file_read,
        write_workspace_file,
    )

    initialize()
    state.ACTIVE_SPRINT_TASK_ID = "T-PATCH-EX"
    state.STEP_FILE_READS.clear()
    write_workspace_file("excerpt.txt", "alpha\nbeta\ngamma")
    record_step_file_read("excerpt.txt", "alpha\nbeta\ngamma")
    result = apply_workspace_patch("excerpt.txt", "totally wrong", "x")
    assert "First lines of current file" in result
    assert "alpha" in result
    assert "read_file" in result


def test_apply_patch_crlf_normalized():
    from backend import state
    from backend.workspace.files import (
        apply_workspace_patch,
        record_step_file_read,
        write_workspace_file,
    )

    initialize()
    state.ACTIVE_SPRINT_TASK_ID = "T-PATCH-CRLF"
    state.STEP_FILE_READS.clear()
    write_workspace_file("crlf.txt", "hello\r\nworld\r\n")
    record_step_file_read("crlf.txt", "hello\r\nworld\r\n")
    result = apply_workspace_patch("crlf.txt", "world\n", "there\n")
    assert "Successfully saved" in result
    assert "there" in state.VIRTUAL_FILESYSTEM["crlf.txt"]


def test_agent_run_lifecycle():
    from backend import state
    from backend.agents.agent_run import finish_run, get_active_run, start_run, update_run

    initialize()
    state.ACTIVE_SPRINT_TASK_ID = "T-RUN"
    run = start_run("T-RUN", "Developer")
    assert get_active_run() is not None
    assert get_active_run().status == "thinking"
    update_run(status="tool_executing", current_tool="read_file")
    assert get_active_run().current_tool == "read_file"
    finish_run(status="completed")
    assert get_active_run() is None
    assert run.run_id


def test_record_task_file_upserts_action():
    from backend import state
    from backend.agents.task_context import init_new_task, record_task_file

    initialize()
    task = init_new_task({"id": "T-FILES", "title": "Files", "description": "d"})
    state.SHARED_BOARD.setdefault("In Progress", []).append(task)
    record_task_file("T-FILES", "lib/auth.dart", "read", persist=False)
    record_task_file("T-FILES", "lib/auth.dart", "written", persist=False)
    assert len(task["files"]) == 1
    assert task["files"][0]["path"] == "lib/auth.dart"
    assert task["files"][0]["action"] == "written"
    assert task["files"][0].get("lastTouchedAt")


def test_sync_task_files_from_transcript():
    from backend import state
    from backend.agents.task_context import init_new_task, sync_task_files_from_transcript

    initialize()
    task = init_new_task({"id": "T-TR", "title": "Transcript", "description": "d"})
    task["transcript"] = [
        {
            "timestamp": "2026-01-01 12:00:00",
            "role": "tool",
            "content": "write_file",
            "toolName": "write_file",
            "toolSuccess": True,
            "toolArgs": {"path": "lib/main.dart", "contentLength": 100},
        },
        {
            "timestamp": "2026-01-01 12:01:00",
            "role": "tool",
            "content": "read_file",
            "toolName": "read_file",
            "toolSuccess": True,
            "toolArgs": {"path": "lib/bloc/auth_bloc.dart"},
        },
    ]
    state.SHARED_BOARD.setdefault("In Progress", []).append(task)
    added = sync_task_files_from_transcript(task)
    assert added == 2
    paths = {f["path"] for f in task["files"]}
    assert "lib/main.dart" in paths
    assert "lib/bloc/auth_bloc.dart" in paths


def test_record_task_git_commit():
    from backend import state
    from backend.agents.task_context import init_new_task, record_task_git_commit

    initialize()
    task = init_new_task({"id": "T-GIT", "title": "Git task", "description": "d"})
    state.SHARED_BOARD.setdefault("Done", []).append(task)
    record_task_git_commit(
        "T-GIT",
        {
            "hash": "abc123def456",
            "message": "T-GIT: Git task",
            "remoteUrl": "https://github.com/org/repo.git",
        },
    )
    assert task["gitCommit"]["hash"] == "abc123def456"
    assert task["gitCommit"]["remoteUrl"] == "https://github.com/org/repo.git"


def test_link_related_features_auth_area():
    from backend import state
    from backend.agents.task_context import init_new_task
    from backend.services.feature_similarity import link_related_features

    initialize()
    existing = init_new_task(
        {
            "id": "T-AUTH-LOGIN",
            "title": "Auth login flow",
            "description": "Implement user authentication login with bloc",
        }
    )
    state.SHARED_BOARD.setdefault("In Progress", []).append(existing)
    new_task = init_new_task(
        {
            "id": "T-AUTH-LOGOUT",
            "title": "Auth logout flow",
            "description": "Implement user authentication logout with bloc",
        }
    )
    linked = link_related_features(new_task, exclude_ids={"T-AUTH-LOGOUT"})
    assert "T-AUTH-LOGIN" in new_task.get("relatedTaskIds", [])
    assert linked
    assert "T-AUTH-LOGIN" in new_task.get("blockedBy", [])


def test_git_commit_returns_hash(monkeypatch):
    from backend.services import git_service

    def fake_run(args, timeout=30):
        if args[:2] == ["commit", "-m"]:
            return {"success": True, "stdout": "", "stderr": "", "returncode": 0}
        if args == ["add", "-A"]:
            return {"success": True, "stdout": "", "stderr": "", "returncode": 0}
        if args == ["rev-parse", "HEAD"]:
            return {"success": True, "stdout": "deadbeef1234567890\n", "stderr": "", "returncode": 0}
        if args == ["remote", "get-url", "origin"]:
            return {
                "success": True,
                "stdout": "https://github.com/example/repo.git\n",
                "stderr": "",
                "returncode": 0,
            }
        return {"success": False, "stdout": "", "stderr": "unknown", "returncode": 1}

    monkeypatch.setattr(git_service, "_run_git", fake_run)
    result = git_service.git_commit("test message")
    assert result["success"] is True
    assert result["hash"] == "deadbeef1234567890"
    assert "github.com" in result["remoteUrl"]


def test_sync_transcript_includes_failed_file_tools():
    from backend import state
    from backend.agents.task_context import init_new_task, sync_task_files_from_transcript

    initialize()
    task = init_new_task({"id": "T-FAIL-F", "title": "Fail file", "description": "d"})
    task["transcript"] = [
        {
            "timestamp": "2026-01-01 12:00:00",
            "role": "tool",
            "content": "write_file failed",
            "toolName": "write_file",
            "toolSuccess": False,
            "toolArgs": {"path": "lib/missing.dart", "contentLength": 0},
        },
    ]
    state.SHARED_BOARD.setdefault("In Progress", []).append(task)
    added = sync_task_files_from_transcript(task)
    assert added == 1
    assert task["files"][0]["path"] == "lib/missing.dart"
    assert task["files"][0]["action"] == "written-failed"


def test_record_task_file_on_failed_tool_via_agent():
    from backend import state
    from backend.agents.task_context import init_new_task
    from backend.services.tool_execution_service import execute_tool

    initialize()
    task = init_new_task({"id": "T-REC-FAIL", "title": "Rec", "description": "d"})
    state.SHARED_BOARD.setdefault("In Progress", []).append(task)
    execute_tool(
        "dev",
        "read_file",
        {"path": "nope.txt"},
        task_id="T-REC-FAIL",
        source="manual",
        skip_approval=True,
        user_prompt="prompt",
    )
    assert any(f.get("path") == "nope.txt" for f in task["files"])
    assert task["files"][0]["action"] == "read-failed"


def test_normalize_tool_arguments_parses_json_string():
    from backend.agents.scrum_agent import _normalize_tool_arguments

    args = _normalize_tool_arguments('{"path": "lib/a.dart", "content": "x"}')
    assert args["path"] == "lib/a.dart"


def test_append_recent_tool_on_run():
    from backend import state
    from backend.agents.agent_run import append_recent_tool, get_active_run, start_run

    initialize()
    state.ACTIVE_SPRINT_TASK_ID = "T-RUN2"
    start_run("T-RUN2", "Developer", max_iterations=4)
    append_recent_tool(
        {
            "toolName": "write_file",
            "toolSuccess": True,
            "toolOutput": "ok",
            "durationMs": 10,
            "timestamp": "2026-01-01",
        }
    )
    run = get_active_run()
    assert run is not None
    assert len(run.recent_tools) == 1
    assert run.recent_tools[0]["toolName"] == "write_file"


def test_dev_step_does_not_auto_advance_without_files(monkeypatch):
    from backend import state
    from backend.agents.task_context import init_new_task
    from backend.services import sprint_service

    initialize()
    task = init_new_task({"id": "T-NOADV", "title": "No files", "description": "d"})
    state.SHARED_BOARD.setdefault("In Progress", []).append(task)

    def fake_execute_step(prompt, max_iterations=8):
        return "Implementation complete."

    monkeypatch.setattr(sprint_service.agent_dev, "execute_step", fake_execute_step)
    sprint_service._run_developer_step(task, "brief")
    assert "T-NOADV" in [t["id"] for t in state.SHARED_BOARD.get("In Progress", [])]
    assert "T-NOADV" not in [t["id"] for t in state.SHARED_BOARD.get("QA", [])]
    assert "T-NOADV" not in [t["id"] for t in state.SHARED_BOARD.get("Code Review", [])]


def test_tool_registry_endpoint():
    initialize()
    client = TestClient(app)
    response = client.get("/api/tools/registry?agent=dev")
    assert response.status_code == 200
    data = response.json()
    assert data["agent"] == "dev"
    names = [t["name"] for t in data["tools"]]
    assert "read_file" in names


def test_manual_tool_execute_read_file():
    from backend import state

    initialize()
    client = TestClient(app)
    state.VIRTUAL_FILESYSTEM["package.json"] = '{"name": "test"}'
    response = client.post(
        "/api/tools/execute",
        json={
            "agent": "dev",
            "toolName": "read_file",
            "arguments": {"path": "package.json"},
        },
    )
    assert response.status_code == 200
    result = response.json()["result"]
    assert result["toolName"] == "read_file"
    assert result["toolSuccess"] is True
    assert "test" in result["toolOutput"]
    assert result["source"] == "manual"


def test_manual_execute_skips_approval_for_write_file():
    from backend import state
    from backend.services.workflow_settings import save_workflow_settings

    initialize()
    save_workflow_settings({"requireToolApproval": True})
    client = TestClient(app)
    response = client.post(
        "/api/tools/execute",
        json={
            "agent": "dev",
            "toolName": "write_file",
            "arguments": {"path": "_manual_test.txt", "content": "hello"},
        },
    )
    assert response.status_code == 200
    result = response.json()["result"]
    assert result["toolSuccess"] is True
    assert "_manual_test.txt" in state.VIRTUAL_FILESYSTEM or response.json()["ok"]


def test_tool_transcript_and_replay():
    from backend import state
    from backend.agents.task_context import init_new_task, record_task_transcript

    initialize()
    client = TestClient(app)
    task = init_new_task({"id": "T-REPLAY", "title": "Replay", "description": "d"})
    state.SHARED_BOARD.setdefault("In Progress", []).append(task)
    record_task_transcript(
        "T-REPLAY",
        "tool",
        "read_file failed",
        agent="Developer",
        toolName="read_file",
        toolSuccess=False,
        toolArgs={"path": "nope-replay.txt"},
        toolOutput="Error: not found",
        source="agent",
    )

    transcript_resp = client.get("/api/tools/transcript/T-REPLAY")
    assert transcript_resp.status_code == 200
    entries = transcript_resp.json()["entries"]
    assert len(entries) == 1
    assert entries[0]["toolName"] == "read_file"

    replay_resp = client.post(
        "/api/tools/replay",
        json={"taskId": "T-REPLAY", "entryIndices": [entries[0]["index"]]},
    )
    assert replay_resp.status_code == 200
    assert replay_resp.json()["executed"] == 1
    results = replay_resp.json()["results"]
    assert results[0]["source"] == "replay"
    assert results[0]["toolName"] == "read_file"


def test_get_tool_history_excludes_transcripts():
    from backend import state
    from backend.agents.task_context import init_new_task, record_task_transcript

    initialize()
    state.TOOL_EXECUTION_LOG.clear()
    task = init_new_task({"id": "T-HIST", "title": "History", "description": "d"})
    state.SHARED_BOARD.setdefault("In Progress", []).append(task)
    record_task_transcript(
        "T-HIST",
        "tool",
        "run_command → flutter analyze ✓",
        agent="Developer",
        toolName="run_command",
        toolSuccess=True,
        toolArgs={"command": "flutter analyze"},
        toolOutput="[success exit 0]\nNo issues",
        source="agent",
    )

    client = TestClient(app)
    resp = client.get("/api/tools/history")
    assert resp.status_code == 200
    events = resp.json()["events"]
    assert not any(e["toolName"] == "run_command" and e["taskId"] == "T-HIST" for e in events)


def test_clear_tool_history_api():
    from backend import state
    from backend.agents.agent_run import finish_run
    from backend.services.tool_execution_service import (
        _tool_log_setting_key,
        append_global_tool_event,
    )

    initialize()
    finish_run()
    state.TOOL_EXECUTION_LOG.clear()
    append_global_tool_event(
        {
            "runId": "run-clear-1",
            "taskId": "T-CLR",
            "agent": "Developer",
            "toolName": "read_file",
            "toolArgs": {"path": "main.py"},
            "toolSuccess": True,
            "toolOutput": "ok",
            "durationMs": 1,
            "timestamp": "2026-07-05 12:00:00",
            "source": "manual",
            "status": "completed",
        }
    )

    client = TestClient(app)
    hist_before = client.get("/api/tools/history")
    assert hist_before.status_code == 200
    assert len(hist_before.json()["events"]) >= 1

    clear_resp = client.post("/api/tools/history/clear")
    assert clear_resp.status_code == 200
    assert clear_resp.json()["ok"] is True
    assert clear_resp.json()["events"] == []

    hist_after = client.get("/api/tools/history")
    assert hist_after.status_code == 200
    assert hist_after.json()["events"] == []

    raw = state.storage.get_setting(_tool_log_setting_key(state.CURRENT_PROJECT_ID))
    assert raw in (None, "", "[]")


def test_clear_chat_history_api():
    from backend import state

    initialize()
    state.storage.save_chat_message(
        state.CURRENT_PROJECT_ID,
        "user",
        "hello from smoke test",
        agent="Developer",
    )
    state.storage.save_chat_message(
        state.CURRENT_PROJECT_ID,
        "assistant",
        "reply from smoke test",
        agent="Developer",
    )

    client = TestClient(app)
    state_before = client.get("/api/state")
    assert state_before.status_code == 200
    assert len(state_before.json()["chatMessages"]) >= 2

    clear_resp = client.post("/api/chat/clear")
    assert clear_resp.status_code == 200
    body = clear_resp.json()
    assert body["ok"] is True
    assert body["deleted"] >= 2
    assert body["chatMessages"] == []

    state_after = client.get("/api/state")
    assert state_after.status_code == 200
    assert state_after.json()["chatMessages"] == []


def test_chat_options_includes_num_ctx():
    from backend.agents.registry import agent_po
    from backend.services.workflow_settings import save_workflow_settings

    initialize()
    save_workflow_settings({"ollamaNumCtx": 32768})
    opts = agent_po._chat_options()
    assert opts["num_ctx"] == 32768
    assert opts["temperature"] == 0.1


def test_configure_po_tools_respects_web_search_flag():
    from backend.agents.registry import agent_po, configure_agent_tools
    from backend.services.workflow_settings import save_workflow_settings

    initialize()
    save_workflow_settings({"enableWebSearch": False, "enableSemanticSearch": False})
    configure_agent_tools()
    names = agent_po.registry.tool_names()
    assert "web_search" not in names
    assert "semantic_search" not in names
    assert "search_code" not in names
    assert "grep" in names
    assert "add_backlog_tasks" in names


def test_configure_agent_tools_preserves_mcp_tools():
    from backend.agents.registry import agent_dev, configure_agent_tools
    from backend.agents.tools import Tool
    from backend.services.mcp_tools import _MCP_TOOL_INSTANCES, clear_mcp_tools, reregister_mcp_tools_on_agents

    initialize()
    clear_mcp_tools()

    def _fake_mcp(**kwargs):
        return "ok"

    fake = Tool(
        name="mcp_test_echo",
        description="test mcp tool",
        parameters={"type": "object", "properties": {}, "required": []},
        func=_fake_mcp,
    )
    _MCP_TOOL_INSTANCES.append(fake)
    reregister_mcp_tools_on_agents()
    assert "mcp_test_echo" in agent_dev.registry.tool_names()

    configure_agent_tools()
    assert "mcp_test_echo" in agent_dev.registry.tool_names()
    assert "read_file" in agent_dev.registry.tool_names()

    clear_mcp_tools()


def test_prompt_budget_scales_with_num_ctx():
    from backend.services.prompt_budget import sprint_file_context_max_chars, truncate_brief

    small = sprint_file_context_max_chars(4096)
    large = sprint_file_context_max_chars(32768)
    assert small < large
    assert small >= 2000

    long_brief = "x" * 20000
    trimmed = truncate_brief(long_brief, 4096)
    assert len(trimmed) < len(long_brief)
    assert "truncated" in trimmed


def test_add_backlog_tasks_split_moves_source_to_done():
    from backend import state
    from backend.agents.task_context import init_new_task
    from backend.services.board_service import append_backlog_tasks

    initialize()
    source = init_new_task({"id": "T-SPLIT", "title": "Big feature", "description": "Too large"})
    state.SHARED_BOARD = {
        "Needs User": [source],
        "Backlog": [],
        "Done": [],
        "In Progress": [],
        "Needs PO": [],
        "QA": [],
    }

    result = append_backlog_tasks(
        [
            {
                "title": "Subtask A",
                "description": "Part A",
                "acceptanceCriteria": ["A works"],
            },
            {
                "title": "Subtask B",
                "description": "Part B",
                "acceptanceCriteria": ["B works"],
            },
        ],
        split_from_task_id="T-SPLIT",
    )
    assert "Added 2 task(s)" in result
    assert any(t["id"] == "T-SPLIT" for t in state.SHARED_BOARD.get("Done", []))
    assert len(state.SHARED_BOARD.get("Backlog", [])) == 2
    backlog = state.SHARED_BOARD["Backlog"]
    assert all("T-SPLIT" in (t.get("relatedTaskIds") or []) for t in backlog)


def test_chat_compose_includes_task_context():
    from backend import state
    from backend.agents.task_context import init_new_task
    from backend.api.chat import _compose_message
    from backend.api.schemas import ChatPayload

    initialize()
    task = init_new_task(
        {
            "id": "T-CHAT",
            "title": "Chat card",
            "description": "Needs clarification",
        }
    )
    task["userQuestion"] = "Which API should we use?"
    state.SHARED_BOARD.setdefault("Needs User", []).append(task)
    state.PROJECT_BRIEF = "Build an app"

    composed = _compose_message(
        ChatPayload(message="Please split this card", agent="po", task_id="T-CHAT")
    )
    assert "T-CHAT" in composed
    assert "Which API should we use?" in composed
    assert "Please split this card" in composed
    assert "add_backlog_tasks" in composed
    assert "never instruct" in composed
    assert "bare array" in composed


def test_chat_po_applies_json_backlog_split(monkeypatch):
    from backend import state
    from backend.agents.registry import agent_po
    from backend.agents.task_context import init_new_task

    initialize()
    source = init_new_task({"id": "T-PO-CHAT", "title": "Big card", "description": "Too big"})
    state.SHARED_BOARD = {
        "Needs User": [source],
        "Backlog": [],
        "Done": [],
        "In Progress": [],
        "Needs PO": [],
        "Code Review": [],
        "QA": [],
    }

    json_response = """[
        {"title": "Sub A", "description": "Part A", "acceptanceCriteria": ["A ok"]},
        {"title": "Sub B", "description": "Part B", "acceptanceCriteria": ["B ok"]}
    ]"""

    monkeypatch.setattr(agent_po, "execute_step", lambda prompt, max_iterations=8: json_response)

    client = TestClient(app)
    resp = client.post(
        "/api/chat",
        json={
            "message": "Please split this card into smaller tasks",
            "agent": "po",
            "task_id": "T-PO-CHAT",
            "ollama_url": "http://localhost:11434",
        },
    )
    assert resp.status_code == 200
    assert len(state.SHARED_BOARD.get("Backlog", [])) == 2
    assert any(t["id"] == "T-PO-CHAT" for t in state.SHARED_BOARD.get("Done", []))


def test_split_task_api_adds_subtasks(monkeypatch):
    from backend import state
    from backend.agents.agent_run import finish_run
    from backend.agents.registry import agent_po
    from backend.agents.task_context import init_new_task

    initialize()
    finish_run()
    source = init_new_task({"id": "T-SPLIT-API", "title": "Big card", "description": "Too big"})
    state.SHARED_BOARD = {
        "Needs User": [source],
        "Backlog": [],
        "Done": [],
        "In Progress": [],
        "Needs PO": [],
        "Code Review": [],
        "QA": [],
    }

    json_response = """[
        {"title": "Sub A", "description": "Part A", "acceptanceCriteria": ["A ok"]},
        {"title": "Sub B", "description": "Part B", "acceptanceCriteria": ["B ok"]}
    ]"""

    monkeypatch.setattr(agent_po, "execute_step", lambda prompt, max_iterations=8: json_response)

    client = TestClient(app)
    resp = client.post(
        "/api/tasks/T-SPLIT-API/split",
        json={"ollama_url": "http://localhost:11434"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["splitResult"]["added"] == 2
    assert len(data["splitResult"]["taskIds"]) == 2
    assert len(state.SHARED_BOARD.get("Backlog", [])) == 2
    assert any(t["id"] == "T-SPLIT-API" for t in state.SHARED_BOARD.get("Done", []))


def test_inject_sprint_context_no_tool_log_event():
    from backend import state
    from backend.agents.task_context import init_new_task
    from backend.services.sprint_service import _inject_sprint_context
    from backend.services.tool_execution_service import get_tool_history

    initialize()
    state.TOOL_EXECUTION_LOG.clear()
    task = init_new_task({"id": "T-CTX", "title": "Ctx", "description": "d"})
    _inject_sprint_context(task, state.PROJECT_BRIEF, "Developer", "Implement the feature.")
    events = get_tool_history()
    assert not any(e.get("source") == "context_inject" for e in events)


def test_inject_sprint_context_records_task_files(monkeypatch):
    from backend import state
    from backend.agents.task_context import init_new_task
    from backend.services.sprint_service import _inject_sprint_context

    initialize()
    task = init_new_task({"id": "T-CTX-F", "title": "Ctx files", "description": "d"})
    state.SHARED_BOARD.setdefault("In Progress", []).append(task)

    monkeypatch.setattr(
        "backend.services.sprint_service.build_sprint_file_context",
        lambda active_task, max_chars=12000: ("=== context ===", ["lib/main.dart", "package.json"]),
    )

    _inject_sprint_context(task, state.PROJECT_BRIEF, "Developer", "Implement.")
    stored = next(t for t in state.SHARED_BOARD["In Progress"] if t["id"] == "T-CTX-F")
    paths = {f["path"] for f in stored.get("files", [])}
    assert "lib/main.dart" in paths
    assert "package.json" in paths
    assert any(
        f.get("action") == "context"
        for f in stored["files"]
        if f.get("path") == "lib/main.dart"
    )


def test_build_state_response_syncs_transcript_files():
    from backend import state
    from backend.agents.task_context import init_new_task

    initialize()
    task = init_new_task({"id": "T-SYNC", "title": "Sync", "description": "d"})
    task["files"] = []
    task["transcript"] = [
        {
            "timestamp": "2026-01-01 12:00:00",
            "role": "tool",
            "content": "read_file",
            "toolName": "read_file",
            "toolSuccess": True,
            "toolArgs": {"path": "lib/widget.dart"},
        }
    ]
    state.SHARED_BOARD = {
        "In Progress": [task],
        "Backlog": [],
        "Done": [],
        "Needs PO": [],
        "Needs User": [],
        "Code Review": [],
        "QA": [],
    }

    client = TestClient(app)
    resp = client.get("/api/state")
    assert resp.status_code == 200
    synced = None
    for lane_tasks in resp.json()["board"].values():
        if not isinstance(lane_tasks, list):
            continue
        for t in lane_tasks:
            if t.get("id") == "T-SYNC":
                synced = t
                break
    assert synced is not None
    paths = {f["path"] for f in synced.get("files", [])}
    assert "lib/widget.dart" in paths


def test_collect_sprint_context_paths_capped(tmp_path, monkeypatch):
    from backend import state
    from backend.agents.task_context import init_new_task
    from backend.workspace.files import _collect_sprint_context_paths

    initialize()
    ws = tmp_path / "ws"
    (ws / "lib").mkdir(parents=True)
    (ws / "tests").mkdir()
    for i in range(10):
        (ws / "lib" / f"file{i}.dart").write_text("x")
    for i in range(10):
        (ws / "tests" / f"test{i}.py").write_text("x")
    (ws / "package.json").write_text("{}")
    monkeypatch.setattr(state, "WORKSPACE_DIR", str(ws))

    task_no_files = init_new_task({"id": "T-NF", "title": "NF", "description": "d"})
    paths = _collect_sprint_context_paths(task_no_files)
    lib_paths = [p for p in paths if p.startswith("lib/")]
    test_paths = [p for p in paths if p.startswith("tests/")]
    assert len(lib_paths) <= 3
    assert len(test_paths) <= 2

    task_with_files = init_new_task(
        {
            "id": "T-WF",
            "title": "WF",
            "description": "d",
        }
    )
    task_with_files["files"] = [{"path": "lib/main.dart", "action": "read"}]
    paths2 = _collect_sprint_context_paths(task_with_files)
    assert "lib/main.dart" in paths2
    extra_lib = [p for p in paths2 if p.startswith("lib/") and p != "lib/main.dart"]
    assert len(extra_lib) == 0


def test_global_tool_log_in_history():
    from backend import state
    from backend.services.tool_execution_service import execute_tool, get_tool_history

    initialize()
    state.TOOL_EXECUTION_LOG.clear()
    execute_tool(
        "dev",
        "read_file",
        {"path": "package.json"},
        task_id="T-GLOG",
        source="manual",
        skip_approval=True,
    )
    events = get_tool_history()
    assert any(
        e["toolName"] == "read_file" and e["taskId"] == "T-GLOG" and e["source"] == "manual"
        for e in events
    )


def test_log_synthetic_tool_in_history():
    from backend import state
    from backend.agents.task_context import init_new_task
    from backend.services.tool_execution_service import get_tool_history, log_synthetic_tool_event

    initialize()
    state.TOOL_EXECUTION_LOG.clear()
    task = init_new_task({"id": "T-SYN", "title": "Syn", "description": "d"})
    state.SHARED_BOARD.setdefault("In Progress", []).append(task)
    log_synthetic_tool_event(
        "T-SYN",
        "QA Tester",
        "run_command",
        tool_args={"command": "flutter test"},
        tool_output="[success exit 0]\nAll tests passed",
        success=True,
        source="orchestrator",
        run_id="run-qa-1",
    )
    events = get_tool_history()
    assert any(
        e["toolName"] == "run_command"
        and e["taskId"] == "T-SYN"
        and e["source"] == "orchestrator"
        and e["runId"] == "run-qa-1"
        for e in events
    )


def test_clear_all_board_tasks():
    from backend import state
    from backend.agents.agent_run import finish_run
    from backend.agents.task_context import init_new_task

    initialize()
    finish_run()
    state.PROJECT_BRIEF = "Keep this brief"
    existing_file = next(iter(state.VIRTUAL_FILESYSTEM.keys()), None)
    state.SHARED_BOARD = {
        "Backlog": [init_new_task({"id": "T-1", "title": "A", "description": "d"})],
        "In Progress": [init_new_task({"id": "T-2", "title": "B", "description": "d"})],
        "Needs PO": [],
        "Needs User": [],
        "QA": [],
        "Done": [],
    }

    client = TestClient(app)
    resp = client.post("/api/board/clear-tasks")
    assert resp.status_code == 200
    assert all(len(tasks) == 0 for tasks in state.SHARED_BOARD.values())
    assert state.PROJECT_BRIEF == "Keep this brief"
    if existing_file:
        assert existing_file in state.VIRTUAL_FILESYSTEM


def test_skills_dir_persists_via_settings():
    from backend import state
    from backend.bootstrap import initialize

    initialize()
    client = TestClient(app)
    custom = "./custom_skills_test"
    resp = client.post(
        "/api/config",
        json={
            "projectName": state.PROJECT_NAME,
            "workspaceDir": state.WORKSPACE_DIR,
            "skillsDir": custom,
            "poModel": "llama3:8b",
            "devModel": "qwen2.5-coder:14b",
            "crModel": "qwen2.5-coder:7b",
            "qaModel": "qwen2.5-coder:7b",
        },
    )
    assert resp.status_code == 200
    assert state.SKILLS_DIR == custom

    initialize()
    assert state.SKILLS_DIR == custom


def test_po_smallest_tasks_guidance_wired():
    from backend.agents.registry import agent_po
    from backend.services.brief_service import PO_SMALLEST_TASKS_GUIDANCE
    from backend.services import sprint_service

    assert "smallest achievable" in PO_SMALLEST_TASKS_GUIDANCE.lower()
    assert PO_SMALLEST_TASKS_GUIDANCE in agent_po.system_prompt
    import inspect

    src = inspect.getsource(sprint_service.run_po_plan)
    assert "PO_SMALLEST_TASKS_GUIDANCE" in src


def test_build_sprint_file_context_includes_task_files():
    import os

    from backend import state
    from backend.agents.task_context import init_new_task
    from backend.bootstrap import initialize
    from backend.workspace.files import build_sprint_file_context

    initialize()
    os.makedirs(os.path.join(state.WORKSPACE_DIR, "lib"), exist_ok=True)
    phys = os.path.join(state.WORKSPACE_DIR, "lib", "main.dart")
    with open(phys, "w", encoding="utf-8") as f:
        f.write("void main() {}")
    task = init_new_task(
        {
            "id": "T-CTX",
            "title": "Ctx",
            "description": "d",
            "files": [{"path": "lib/main.dart", "action": "written"}],
        }
    )
    block, paths = build_sprint_file_context(task, max_chars=8000)
    assert "lib/main.dart" in paths
    assert "void main()" in block
    assert "PRE-LOADED FILE CONTEXT" in block


def test_build_sprint_file_context_truncates():
    from backend import state
    from backend.agents.task_context import init_new_task
    from backend.bootstrap import initialize
    from backend.workspace.files import build_sprint_file_context

    initialize()
    state.VIRTUAL_FILESYSTEM["big.txt"] = "x" * 5000
    task = init_new_task({"id": "T-TRUNC", "title": "T", "description": "d", "files": ["big.txt"]})
    block, _paths = build_sprint_file_context(task, max_chars=500)
    assert "[truncated]" in block or len(block) <= 600


def test_derive_project_test_commands():
    import os

    from backend import state
    from backend.bootstrap import initialize
    from backend.workspace.files import derive_project_test_commands

    initialize()
    ws = state.WORKSPACE_DIR
    os.makedirs(ws, exist_ok=True)

    open(os.path.join(ws, "pubspec.yaml"), "w", encoding="utf-8").write("name: test\n")
    cmds = derive_project_test_commands()
    assert "flutter analyze" in cmds
    assert "flutter test" in cmds

    os.remove(os.path.join(ws, "pubspec.yaml"))
    open(os.path.join(ws, "package.json"), "w", encoding="utf-8").write('{"name":"t"}')
    cmds = derive_project_test_commands()
    assert cmds == ["npm test"]

    os.remove(os.path.join(ws, "package.json"))
    os.makedirs(os.path.join(ws, "tests"), exist_ok=True)
    open(os.path.join(ws, "tests", "test_a.py"), "w", encoding="utf-8").write("def test_x(): pass\n")
    state.VIRTUAL_FILESYSTEM["tests/test_a.py"] = "def test_x(): pass\n"
    cmds = derive_project_test_commands()
    assert "pytest tests/ -q" in cmds


def test_qa_step_passed_playbook_failure():
    from backend.agents.task_context import init_new_task
    from backend.services import sprint_service

    task = init_new_task({"id": "T-QA1", "title": "Q", "description": "d"})
    playbook = {"run": True, "commands": ["pytest"], "passed": False, "results": []}
    passed, reason = sprint_service._qa_step_passed(task, "All good", playbook, "2026-01-01 00:00:00")
    assert passed is False
    assert "playbook" in reason.lower()


def test_qa_step_passed_playbook_success():
    from backend.agents.task_context import init_new_task, record_task_transcript
    from backend.services import sprint_service

    task = init_new_task({"id": "T-QA2", "title": "Q", "description": "d"})
    playbook = {"run": True, "commands": ["npm test"], "passed": True, "results": []}
    passed, _reason = sprint_service._qa_step_passed(task, "Looks fine", playbook, "2026-01-01 00:00:00")
    assert passed is True


def test_qa_gate_blocks_update_board_to_done():
    from backend import state
    from backend.agents.task_context import init_new_task
    from backend.bootstrap import initialize
    from backend.services.sprint_service import qa_gate_blocks_done

    initialize()
    task = init_new_task({"id": "T-GATE", "title": "G", "description": "d"})
    task["qaEvidence"] = {"playbookRun": True, "commands": ["npm test"], "passed": False}
    state.SHARED_BOARD.setdefault("QA", []).append(task)
    state.ACTIVE_SPRINT_AGENT = "QA Tester"
    state.SPRINT_STEP_STARTED_AT = "2026-01-01 00:00:00"

    blocked, reason = qa_gate_blocks_done(task)
    assert blocked is True
    assert "playbook" in reason.lower() or "test" in reason.lower()

    from backend.agents.registry import _guarded_update_board

    result = _guarded_update_board("T-GATE", "Done")
    assert "Error:" in result
    assert "T-GATE" in [t["id"] for t in state.SHARED_BOARD.get("QA", [])]


def test_publish_sprint_progress_emits_event():
    from backend.services import sprint_service

    captured = []

    def fake_publish(event_type, data):
        captured.append((event_type, data))

    sprint_service.publish_event = fake_publish
    sprint_service.publish_sprint_progress(
        phase="po_plan",
        step=0,
        max_steps=10,
        agent="Product Owner",
        task_id="PLANNING",
        task_title="Test",
    )
    assert captured
    assert captured[0][0] == "sprint_progress"
    assert captured[0][1]["phase"] == "po_plan"


def test_run_po_plan_sets_planning_context(monkeypatch):
    from backend import state
    from backend.bootstrap import initialize
    from backend.services import sprint_service

    initialize()
    state.SPRINT_CANCEL = False
    progress_events = []
    monkeypatch.setattr(
        sprint_service,
        "publish_event",
        lambda t, d: progress_events.append((t, d)) if t == "sprint_progress" else None,
    )

    def fake_execute_step(prompt, max_iterations=8):
        assert state.ACTIVE_SPRINT_TASK_ID == sprint_service.PLANNING_TASK_ID
        assert state.ACTIVE_SPRINT_AGENT == "Product Owner"
        return "SIMULATION_FALLBACK"

    monkeypatch.setattr(sprint_service.agent_po, "execute_step", fake_execute_step)
    sprint_service.run_po_plan("Build a todo app", "http://localhost:11434")

    assert any(e[1].get("phase") == "po_plan" for e in progress_events)
    assert state.ACTIVE_SPRINT_TASK_ID is None


def test_run_plan_and_run_cancel_skips_sprint(monkeypatch):
    from backend import state
    from backend.bootstrap import initialize
    from backend.services import sprint_service

    initialize()

    auto_called = []

    def fake_auto_sprint(brief, ollama_url, max_steps=None):
        auto_called.append(True)
        return {"stepsRun": 0, "status": "cancelled"}

    def fake_po_plan(brief, ollama_url):
        state.SPRINT_CANCEL = True

    monkeypatch.setattr(sprint_service, "run_auto_sprint", fake_auto_sprint)
    monkeypatch.setattr(sprint_service, "run_po_plan", fake_po_plan)

    summary = sprint_service.run_plan_and_run("brief", "http://localhost:11434", max_steps=5)
    assert not auto_called
    assert summary.get("status") == "cancelled"


def test_clear_logs_api():
    from backend import state
    from backend.services.logs import add_system_log

    initialize()
    client = TestClient(app)
    add_system_log("System", "info", "before clear")
    assert len(state.SYSTEM_LOGS) >= 1

    clear_resp = client.post("/api/logs/clear")
    assert clear_resp.status_code == 200
    assert clear_resp.json().get("ok") is True
    assert clear_resp.json().get("logs") == []

    state_resp = client.get("/api/state")
    assert state_resp.status_code == 200
    assert state_resp.json().get("logs") == []


def test_web_search_disabled_by_default():
    from backend.services.workflow_settings import save_workflow_settings
    from backend.workspace.web_search import web_search

    initialize()
    save_workflow_settings({"enableWebSearch": False})
    result = web_search("python asyncio tutorial")
    assert "disabled" in result.lower()


def test_web_search_mocked(monkeypatch):
    from backend.services.workflow_settings import save_workflow_settings
    from backend.workspace import web_search as ws_mod

    initialize()
    save_workflow_settings({"enableWebSearch": True})

    class FakeResp:
        text = (
            '<a class="result__a">Example</a>'
            '<div class="result__snippet">Async patterns in Python</div>'
        )

        def raise_for_status(self):
            return None

    monkeypatch.setattr(ws_mod.requests, "get", lambda *a, **k: FakeResp())
    result = ws_mod.web_search("python asyncio", max_results=3)
    assert "Example" in result or "Async" in result


def test_autonomous_mode_needs_user_cap():
    from backend import state
    from backend.agents.task_context import init_new_task
    from backend.services.sprint_service import _try_move_to_needs_user
    from backend.services.workflow_settings import save_workflow_settings

    initialize()
    save_workflow_settings({"autonomousMode": True, "maxNeedsUserPerSprint": 1})
    state.SPRINT_NEEDS_USER_COUNT = 0
    state.SHARED_BOARD = {
        "Backlog": [],
        "In Progress": [init_new_task({"id": "T1", "title": "Task", "description": "Desc"})],
        "Needs PO": [],
        "Needs User": [],
        "QA": [],
        "Done": [],
    }
    task = state.SHARED_BOARD["In Progress"][0]
    assert _try_move_to_needs_user(task["id"], task, "Need input") is True
    assert len(state.SHARED_BOARD.get("Needs User", [])) == 1

    task2 = init_new_task({"id": "T2", "title": "Task 2", "description": "Desc"})
    state.SHARED_BOARD["In Progress"].append(task2)
    assert _try_move_to_needs_user(task2["id"], task2, "Need input again") is False
    assert len(state.SHARED_BOARD.get("Needs User", [])) == 1


def test_load_project_preserves_skills_in_db():
    from backend import state
    from backend.agents.registry import agent_dev, agent_po
    from backend.bootstrap import initialize, load_project_into_state
    from backend.services.project_service import save_current_project_state

    initialize()
    pid = state.CURRENT_PROJECT_ID

    agent_po.assigned_skills = ["po_skill.md"]
    agent_dev.assigned_skills = ["dev_skill.md"]
    agent_po.model = "custom-po:7b"
    agent_dev.model = "custom-dev:14b"
    save_current_project_state()

    agent_po.assigned_skills = []
    agent_dev.assigned_skills = []
    agent_po.model = "llama3:8b"
    agent_dev.model = "qwen2.5-coder:14b"

    assert load_project_into_state(pid) is True

    proj = state.storage.load_project(pid)
    assert proj is not None
    assert proj["po_skills"] == ["po_skill.md"]
    assert proj["dev_skills"] == ["dev_skill.md"]
    assert proj["po_model"] == "custom-po:7b"
    assert proj["dev_model"] == "custom-dev:14b"
    assert agent_po.assigned_skills == ["po_skill.md"]
    assert agent_dev.assigned_skills == ["dev_skill.md"]
    assert agent_po.model == "custom-po:7b"


def test_workflow_settings_preserves_models_and_skills():
    from backend import state
    from backend.agents.registry import agent_po, agent_qa
    from backend.bootstrap import initialize
    from backend.services.project_service import save_current_project_state

    initialize()
    client = TestClient(app)

    agent_po.assigned_skills = ["planning.md"]
    agent_qa.assigned_skills = ["testing.md"]
    agent_po.model = "custom-po:8b"
    agent_qa.model = "custom-qa:7b"
    save_current_project_state()

    response = client.post(
        "/api/workflow/settings",
        json={"autonomousMode": True, "enableWebSearch": True},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["assignedSkills"]["po"] == ["planning.md"]
    assert data["assignedSkills"]["qa"] == ["testing.md"]
    assert data["models"]["po"] == "custom-po:8b"
    assert data["models"]["qa"] == "custom-qa:7b"
    assert data["workflowSettings"]["autonomousMode"] is True
    assert data["workflowSettings"]["enableWebSearch"] is True

    proj = state.storage.load_project(state.CURRENT_PROJECT_ID)
    assert proj["po_skills"] == ["planning.md"]
    assert proj["qa_model"] == "custom-qa:7b"


def test_grep_tool_finds_pattern(tmp_path, monkeypatch):
    from backend import state
    from backend.workspace.files import grep_workspace, sync_virtual_filesystem_from_disk

    initialize()
    ws = tmp_path / "ws"
    (ws / "lib").mkdir(parents=True)
    (ws / "lib" / "main.dart").write_text("void main() {\n  print('hello');\n}\n")
    monkeypatch.setattr(state, "WORKSPACE_DIR", str(ws))
    state.VIRTUAL_FILESYSTEM.clear()
    sync_virtual_filesystem_from_disk()
    monkeypatch.setattr("backend.workspace.files.shutil.which", lambda _: None)
    results = grep_workspace("void main", limit=10)
    assert results, f"VFS keys: {list(state.VIRTUAL_FILESYSTEM.keys())}"
    assert any("main.dart" in r["path"].replace("\\", "/") for r in results)


def test_glob_file_search_lists_files(tmp_path, monkeypatch):
    from backend import state
    from backend.workspace.files import glob_workspace

    initialize()
    ws = tmp_path / "ws"
    (ws / "lib").mkdir(parents=True)
    (ws / "lib" / "a.dart").write_text("x")
    (ws / "lib" / "b.dart").write_text("y")
    monkeypatch.setattr(state, "WORKSPACE_DIR", str(ws))
    paths = glob_workspace("**/*.dart", limit=20)
    assert "lib/a.dart" in paths
    assert "lib/b.dart" in paths


def test_work_type_skips_dev_claim():
    from backend import state
    from backend.agents.task_context import init_new_task, next_claimable_backlog_task

    initialize()
    planning = init_new_task(
        {
            "id": "T-PLAN",
            "title": "Decompose brief into backlog",
            "description": "PO planning work",
            "workType": "planning",
            "requiresDev": False,
        }
    )
    dev_task = init_new_task(
        {
            "id": "T-DEV",
            "title": "Implement login",
            "description": "Build login screen",
            "workType": "implementation",
            "requiresDev": True,
        }
    )
    state.SHARED_BOARD = {
        "Backlog": [planning, dev_task],
        "In Progress": [],
        "Done": [],
        "Needs PO": [],
        "Needs User": [],
        "QA": [],
    }
    claimed = next_claimable_backlog_task()
    assert claimed is not None
    assert claimed["id"] == "T-DEV"


def test_llm_debug_log_persisted():
    from backend import state
    from backend.services.llm_debug_log import append_llm_log_entry, clear_llm_log, get_llm_logs

    initialize()
    clear_llm_log()
    append_llm_log_entry(
        agent="Developer",
        agent_id="dev",
        task_id="T-LOG",
        model="test-model",
        iteration=1,
        request_messages=[{"role": "user", "content": "hello"}],
        response_content="world",
        duration_ms=10,
    )
    logs = get_llm_logs(limit=10)
    assert len(logs) >= 1
    assert logs[0]["responseContent"] == "world"

    client = TestClient(app)
    resp = client.get("/api/ollama/logs")
    assert resp.status_code == 200
    assert len(resp.json()["entries"]) >= 1


def test_diagnose_task_api(monkeypatch):
    from backend import state
    from backend.agents.task_context import init_new_task

    initialize()
    task = init_new_task({"id": "T-DX", "title": "Stuck task", "description": "Fails tests"})
    state.SHARED_BOARD.setdefault("In Progress", []).append(task)

    class FakeMessage:
        content = (
            '{"summary":"Tests failing","problem":"QA gate","rootCause":"tests",'
            '"evidence":["run_command failed"],"recommendedAction":"Fix tests","suggestedAgent":"dev"}'
        )
        tool_calls = None

    class FakeResponse:
        message = FakeMessage()

    monkeypatch.setattr(
        "backend.services.task_diagnosis.agent_po._chat",
        lambda *a, **k: FakeResponse(),
    )

    client = TestClient(app)
    resp = client.post("/api/tasks/T-DX/diagnose", json={"ollamaUrl": "http://localhost:11434"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["diagnosis"]["rootCause"] == "tests"
    assert "state" in body
    board_tasks = [t for lane in body["state"]["board"].values() for t in lane if isinstance(t, dict)]
    dx_task = next((t for t in board_tasks if t.get("id") == "T-DX"), None)
    assert dx_task is not None
    assert dx_task.get("lastDiagnosis", {}).get("rootCause") == "tests"


def test_retry_step_same_mode(monkeypatch):
    from backend import state
    from backend.agents.task_context import init_new_task

    initialize()
    task = init_new_task({"id": "T-RT", "title": "Retry me", "description": "d"})
    state.SHARED_BOARD.setdefault("In Progress", []).append(task)

    monkeypatch.setattr(
        "backend.agents.registry.agent_dev.execute_step",
        lambda prompt, max_iterations=8: "Retry succeeded",
    )

    client = TestClient(app)
    resp = client.post(
        "/api/agents/retry-step",
        json={
            "taskId": "T-RT",
            "agentId": "dev",
            "mode": "same",
            "ollamaUrl": "http://localhost:11434",
        },
    )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


def test_delete_file_requires_approval_when_enabled():
    from backend import state
    from backend.services.tool_execution_service import execute_tool
    from backend.services.workflow_settings import save_workflow_settings

    initialize()
    save_workflow_settings({"requireToolApproval": True, "toolApprovalTools": ["delete_file"]})
    state.VIRTUAL_FILESYSTEM["temp.txt"] = "delete me"
    result = execute_tool(
        "dev",
        "delete_file",
        {"path": "temp.txt"},
        task_id="T-DEL",
        source="manual",
        skip_approval=False,
    )
    assert result.success is False or "approval" in result.tool_output.lower() or "await" in result.tool_output.lower()


def test_tool_cache_read_file_hit(monkeypatch):
    from backend import state
    from backend.services.tool_cache import clear_tool_cache, store_cached_result
    from backend.services.tool_execution_service import execute_tool

    initialize()
    clear_tool_cache()
    state.ACTIVE_SPRINT_TASK_ID = "T-CACHE"
    state.VIRTUAL_FILESYSTEM["lib/a.dart"] = "hello"

    calls = []

    def fake_invoke(name, args):
        calls.append((name, args))
        return "live content"

    from backend.agents.registry import AGENT_MAP

    agent = AGENT_MAP["dev"]
    original_invoke = agent.registry.invoke
    agent.registry.invoke = fake_invoke
    try:
        store_cached_result("read_file", {"path": "lib/a.dart"}, "cached content", True)
        result = execute_tool(
            "dev",
            "read_file",
            {"path": "lib/a.dart"},
            task_id="T-CACHE",
            source="agent",
            skip_approval=True,
        )
        assert result.success
        assert "cached content" in result.tool_output
        assert len(calls) == 0
    finally:
        agent.registry.invoke = original_invoke


def test_run_command_lint_cache_blocks_repeat():
    from backend.services.tool_cache import clear_tool_cache, store_cached_result

    clear_tool_cache()
    output = "[findings exit 1]\n  error • bad • lib/main.dart:1:1\n"
    store_cached_result("run_command", {"command": "flutter analyze"}, output, True)
    blocked = __import__(
        "backend.services.tool_cache", fromlist=["check_run_command_cache"]
    ).check_run_command_cache("flutter analyze", {"command": "flutter analyze"})
    assert blocked is not None
    assert "unchanged" in blocked.lower() or "problem" in blocked.lower()


def test_dev_gate_blocks_advance_with_diagnostics(monkeypatch):
    from backend.agents.task_context import init_new_task
    from backend.services.sprint_service import dev_gate_blocks_advance

    initialize()
    task = init_new_task({"id": "T-LINT", "title": "Lint gate", "description": "d"})
    task["lastCommandDiagnostics"] = [
        {"file": "lib/a.dart", "line": 1, "column": 1, "severity": "error", "message": "x"}
    ]

    monkeypatch.setattr(
        "backend.services.sprint_service.get_workflow_settings",
        lambda: {"requireCleanLint": True},
    )
    blocked, reason = dev_gate_blocks_advance(task)
    assert blocked
    assert "lint" in reason.lower()


def test_fix_verify_loop_disabled_delegates(monkeypatch):
    from backend.agents.task_context import init_new_task
    from backend.services.fix_verify_loop import run_fix_verify_loop

    initialize()
    task = init_new_task({"id": "T-FV", "title": "FV", "description": "d"})
    calls = []

    class FakeAgent:
        role = "Developer"

        def execute_step(self, prompt, max_iterations=8):
            calls.append((prompt[:40], max_iterations))
            return "done"

    result = run_fix_verify_loop(FakeAgent(), task, "prompt", max_iterations=6)
    assert result == "done"
    assert len(calls) == 1


def test_memory_scoped_agent_id():
    from backend import state
    from backend.storage.memory_engine import SemanticMemoryEngine

    initialize()
    engine = SemanticMemoryEngine()
    state.CURRENT_PROJECT_ID = "proj-a"
    scoped = engine._scoped_agent_id("Developer", "proj-a")
    assert scoped == "proj-a:Developer"
    engine.save_outcome("Developer", "fixed lib/main.dart", "fix_pattern", project_id="proj-a")
    hits = engine.search("Developer", "fixed lib/main", limit=3, project_id="proj-a")
    assert hits
    assert hits[0]["category"] == "fix_pattern"


def test_semantic_search_qdrant_optional():
    import pytest
    from backend.storage.code_index import CodeIndexEngine

    initialize()
    engine = CodeIndexEngine()
    status = engine.index_status()
    if not status.get("available"):
        pytest.skip("Qdrant not running")
    result = engine.index_workspace()
    if not result.get("ok"):
        pytest.skip(result.get("error", "index failed"))
    hits = engine.search("main function", limit=3)
    assert isinstance(hits, list)

