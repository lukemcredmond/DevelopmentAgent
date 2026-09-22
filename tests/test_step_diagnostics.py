"""Step diagnostics JSON files under ALLHANDS_HOME/diagnostics/."""

import json
import os
from pathlib import Path
from unittest.mock import patch

from backend import state
from backend.agents.task_context import init_new_task
from backend.bootstrap import initialize
from backend.services.sprint_service import run_in_progress_step
from backend.services.step_diagnostics import (
    build_card_work_snapshot,
    classify_tool_failure,
    clear_active_step_trace,
    derive_exit_reason,
    finalize_active_step_trace,
    format_console_ollama_wait,
    format_ollama_wait_event,
    get_active_trace,
    log_event,
    record_phase_graph,
    record_po_json_applied,
    record_sampling_snapshot,
    start_step_trace,
    log_ollama_call,
)


def test_tracker_writes_json_file(tmp_path, monkeypatch):
    monkeypatch.setenv("ALLHANDS_HOME", str(tmp_path))
    initialize()
    state.CURRENT_PROJECT_ID = "test-proj"

    trace = start_step_trace("T-1", "Feature X", "Developer", "In Progress")
    trace.log_ollama_call(1, duration_ms=1200, tool_calls=["read_file"], text_chars=0)
    trace.log_tool("read_file", True, "lib/main.dart")
    trace.log_event("text_rejected", "plan rejected")
    state.LAST_STEP_OUTCOME = {
        "taskId": "T-1",
        "ok": False,
        "message": "read only",
        "toolFailures": 0,
        "laneBefore": "In Progress",
        "laneAfter": "In Progress",
        "agent": "Developer",
    }
    state.DEV_STEP_READ_ONLY_NO_EDITS = True

    summary = finalize_active_step_trace(lane_after="In Progress")

    assert summary is not None
    assert trace.file_path.is_file()
    data = json.loads(trace.file_path.read_text(encoding="utf-8"))
    assert data["status"] == "complete"
    assert data["exitReason"] == "read_only_no_edits"
    assert data["ollamaCalls"][0]["toolCalls"] == ["read_file"]
    assert data["taskId"] == "T-1"
    assert "filePath" in data
    assert get_active_trace() is None
    assert state.LAST_STEP_DIAGNOSTICS is not None


def test_trace_does_not_merge_other_tasks_global_progress(tmp_path, monkeypatch):
    monkeypatch.setenv("ALLHANDS_HOME", str(tmp_path))
    initialize()
    state.LAST_STEP_PROGRESS = {
        "taskId": "OTHER",
        "devPhaseGraph": {"cycle": 999, "phase": "explore"},
    }
    trace = start_step_trace("T-SCOPED", "Scoped", "Developer", "In Progress")
    summary = finalize_active_step_trace(
        lane_after="In Progress",
        agent_result="Stopped: phase cycle cap reached",
    )
    assert summary is not None
    data = json.loads(trace.file_path.read_text(encoding="utf-8"))
    assert data["exitReason"] == "phase_cycle_cap"
    assert "stepProgress" not in data


def test_steps_on_card_uses_durable_dev_visits():
    task = init_new_task({"id": "T-VISITS", "title": "Visits", "description": "d"})
    task["devStepCount"] = 7
    task["stuckLoops"] = 1
    snapshot = build_card_work_snapshot(task)
    assert snapshot["stepsOnCard"] == 7
    assert snapshot["stuckLoops"] == 1


def test_run_in_progress_logs_diagnostics_path(tmp_path, monkeypatch):
    monkeypatch.setenv("ALLHANDS_HOME", str(tmp_path))
    initialize()
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

    task = init_new_task({"id": "T-DIAG", "title": "Diag task", "description": "d", "status": "In Progress"})
    state.SHARED_BOARD["In Progress"] = [task]
    state.SYSTEM_LOGS.clear()

    def fake_dev_step(active_task, *_args, **_kwargs):
        state.LAST_AGENT_STEP_RESULT = "done"

    with patch("backend.services.sprint_service._run_developer_step", side_effect=fake_dev_step):
        run_in_progress_step("brief", "http://localhost:11434")

    diag_logs = [log for log in state.SYSTEM_LOGS if "Step diagnostics:" in log.get("text", "")]
    assert len(diag_logs) >= 1
    assert state.LAST_STEP_DIAGNOSTICS is not None
    assert Path(state.LAST_STEP_DIAGNOSTICS["filePath"]).is_file()


def test_diagnostics_api_latest(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from backend.main import app

    monkeypatch.setenv("ALLHANDS_HOME", str(tmp_path))
    initialize()
    state.LAST_STEP_DIAGNOSTICS = {
        "traceId": "ABC",
        "filePath": str(tmp_path / "step.json"),
        "exitReason": "read_only_no_edits",
    }

    client = TestClient(app)
    res = client.get("/api/sprint/diagnostics/latest")
    assert res.status_code == 200
    assert res.json()["diagnostics"]["traceId"] == "ABC"

    state.LAST_STEP_DIAGNOSTICS = None
    res404 = client.get("/api/sprint/diagnostics/latest")
    assert res404.status_code == 404


def test_build_hint_tool_output_echo(tmp_path, monkeypatch):
    monkeypatch.setenv("ALLHANDS_HOME", str(tmp_path))
    initialize()
    trace = start_step_trace("T-ECHO", "Echo", "Developer", "In Progress")
    hint = trace._build_hint("tool_output_echo")
    assert "repeated prior tool output" in hint.lower()
    clear_active_step_trace()


def test_checkpoint_written_on_start(tmp_path, monkeypatch):
    monkeypatch.setenv("ALLHANDS_HOME", str(tmp_path))
    initialize()
    state.CURRENT_PROJECT_ID = "test-proj"

    trace = start_step_trace("T-1", "Feature X", "Developer", "In Progress")

    assert trace.file_path.is_file()
    data = json.loads(trace.file_path.read_text(encoding="utf-8"))
    assert data["status"] == "running"
    assert data["taskId"] == "T-1"
    live_logs = [log for log in state.SYSTEM_LOGS if "Step diagnostics (live):" in log.get("text", "")]
    assert len(live_logs) >= 1
    clear_active_step_trace()


def test_checkpoint_updated_after_tool_start(tmp_path, monkeypatch):
    monkeypatch.setenv("ALLHANDS_HOME", str(tmp_path))
    initialize()
    state.CURRENT_PROJECT_ID = "test-proj"

    trace = start_step_trace("T-2", "Tool event", "Developer", "In Progress")
    trace.log_event("tool_start", "read_file — pubspec.yaml")

    data = json.loads(trace.file_path.read_text(encoding="utf-8"))
    assert data["status"] == "running"
    assert any(event["kind"] == "tool_start" for event in data["events"])
    assert "read_file" in data["lastEvent"]
    clear_active_step_trace()


def test_auto_sprint_dev_step_writes_diagnostics(tmp_path, monkeypatch):
    monkeypatch.setenv("ALLHANDS_HOME", str(tmp_path))
    initialize()
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

    task = init_new_task({"id": "T-AUTO", "title": "Auto sprint", "description": "d", "status": "In Progress"})
    state.SHARED_BOARD["In Progress"] = [task]
    state.SPRINT_PROGRESS_MAX = 20
    state.SPRINT_PROGRESS_STEP = 1
    state.SYSTEM_LOGS.clear()
    clear_active_step_trace()
    state.LAST_STEP_DIAGNOSTICS = None

    def fake_fix_verify(*_args, **_kwargs):
        return "done"

    from backend.services.sprint_service import _run_developer_step

    with patch("backend.services.fix_verify_loop.run_fix_verify_loop", side_effect=fake_fix_verify):
        _run_developer_step(dict(task), "brief")

    assert state.LAST_STEP_DIAGNOSTICS is not None
    assert Path(state.LAST_STEP_DIAGNOSTICS["filePath"]).is_file()
    data = json.loads(Path(state.LAST_STEP_DIAGNOSTICS["filePath"]).read_text(encoding="utf-8"))
    assert data["status"] == "complete"
    assert data["taskId"] == "T-AUTO"
    assert get_active_trace() is None


def test_tool_end_logged_after_read_file(tmp_path, monkeypatch):
    from backend.agents.registry import agent_dev
    from backend.services.tool_execution_service import execute_tool

    monkeypatch.setenv("ALLHANDS_HOME", str(tmp_path))
    initialize()
    state.CURRENT_PROJECT_ID = "test-proj"
    start_step_trace("T-TOOL", "Tool end", "Developer", "In Progress")

    with patch.object(agent_dev.registry, "invoke", return_value="name: test_app"):
        execute_tool(
            "dev",
            "read_file",
            {"path": "pubspec.yaml"},
            task_id="T-TOOL",
            source="agent",
        )

    trace = get_active_trace()
    assert trace is not None
    assert any(event["kind"] == "tool_end" for event in trace.events)
    assert any(entry["toolName"] == "read_file" for entry in trace.tools_log)
    clear_active_step_trace()


def test_run_in_progress_does_not_hold_state_lock(tmp_path, monkeypatch):
    import threading
    import time

    monkeypatch.setenv("ALLHANDS_HOME", str(tmp_path))
    initialize()
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

    task = init_new_task({"id": "T-LOCK", "title": "Lock test", "description": "d", "status": "In Progress"})
    state.SHARED_BOARD["In Progress"] = [task]

    dev_started = threading.Event()
    poll_ok = threading.Event()

    def fake_dev_step(_active_task, *_args, **_kwargs):
        dev_started.set()
        time.sleep(0.4)
        state.LAST_AGENT_STEP_RESULT = "done"

    def poll_lock():
        dev_started.wait(timeout=2)
        acquired = state.STATE_LOCK.acquire(timeout=0.25)
        if acquired:
            state.STATE_LOCK.release()
            poll_ok.set()

    with patch("backend.services.sprint_service._run_developer_step", side_effect=fake_dev_step):
        runner = threading.Thread(
            target=lambda: run_in_progress_step("brief", "http://localhost:11434"),
            daemon=True,
        )
        poller = threading.Thread(target=poll_lock, daemon=True)
        runner.start()
        poller.start()
        poller.join(timeout=2)
        runner.join(timeout=5)

    assert poll_ok.is_set()


def test_sampling_and_ollama_call_fields(tmp_path, monkeypatch):
    monkeypatch.setenv("ALLHANDS_HOME", str(tmp_path))
    initialize()
    state.CURRENT_PROJECT_ID = "test-proj"
    clear_active_step_trace()

    trace = start_step_trace("T-SAMP", "Sampling", "Developer", "In Progress")
    record_sampling_snapshot(
        {
            "model": "gemma-4-q4km:26b",
            "provider": "ollama",
            "temperature": 0.15,
            "num_predict": 2048,
            "num_ctx": 8192,
        }
    )
    trace.log_ollama_call(
        1,
        duration_ms=90000,
        tool_calls=["read_file"],
        eval_tokens=2048,
        prompt_tokens=4000,
        tokens_reported=True,
        num_predict=2048,
        num_ctx=8192,
        done_reason="length",
        prompt_eval_ms=1200,
        eval_ms=88000,
    )
    data = json.loads(trace.file_path.read_text(encoding="utf-8"))
    assert data["sampling"]["num_predict"] == 2048
    assert data["sampling"]["model"] == "gemma-4-q4km:26b"
    call = data["ollamaCalls"][0]
    assert call["numPredict"] == 2048
    assert call["numCtx"] == 8192
    assert call["doneReason"] == "length"
    assert call["truncated"] is True
    assert call["evalMs"] == 88000
    clear_active_step_trace()


def test_write_rollup_and_max_iterations_after_writes(tmp_path, monkeypatch):
    monkeypatch.setenv("ALLHANDS_HOME", str(tmp_path))
    initialize()
    state.CURRENT_PROJECT_ID = "test-proj"
    state.DEV_STEP_READ_ONLY_NO_EDITS = False
    clear_active_step_trace()

    trace = start_step_trace("T-WRITE", "Writes", "Developer", "In Progress")
    trace.log_tool("apply_patch", True, "lib/a.dart (replace 12 chars)")
    trace.log_tool("apply_patch", False, "old_text mismatch on lib/a.dart")
    trace.log_tool("run_command", False, "cd mealplanner && flutter test\nexit 1")
    record_phase_graph(
        {
            "phase": "verify",
            "exploreCount": 0,
            "patchCount": 2,
            "verifyCount": 0,
            "writeSucceeded": True,
            "cycle": 1,
            "forcedPatch": True,
        }
    )
    task = init_new_task({"id": "T-WRITE", "title": "Writes", "status": "In Progress"})
    task["forcePatchNextDevStep"] = True
    task["devStepCount"] = 2
    state.SHARED_BOARD.setdefault("In Progress", [])
    state.SHARED_BOARD["In Progress"] = [t for t in state.SHARED_BOARD["In Progress"] if t.get("id") != "T-WRITE"]
    state.SHARED_BOARD["In Progress"].append(task)

    reason = derive_exit_reason(
        agent_result="Max tool iterations (6) reached",
        tools_used={"apply_patch", "run_command"},
        lane_before="In Progress",
        lane_after="In Progress",
    )
    assert reason == "max_iterations_after_writes"

    state.LAST_STEP_OUTCOME = {"ok": False, "taskId": "T-WRITE"}
    summary = finalize_active_step_trace(
        lane_after="In Progress",
        agent_result="Max tool iterations (6) reached",
    )
    assert summary is not None
    data = json.loads(Path(summary["filePath"]).read_text(encoding="utf-8"))
    assert data["exitReason"] == "max_iterations_after_writes"
    assert data["writesAttempted"] == 2
    assert data["writesSucceeded"] == 1
    assert "lib/a.dart" in data["writePaths"]
    assert "patch_mismatch" in data["toolFailureClasses"]
    assert "command_nonzero" in data["toolFailureClasses"]
    assert data["cardCumulativeState"]["forcePatchNextDevStep"] is True
    assert data["cardCumulativeState"]["phaseGraph"]["writeSucceeded"] is True
    assert "wrote files" in data["hint"].lower()


def test_max_iterations_without_successful_write(tmp_path, monkeypatch):
    monkeypatch.setenv("ALLHANDS_HOME", str(tmp_path))
    initialize()
    state.CURRENT_PROJECT_ID = "test-proj"
    state.DEV_STEP_READ_ONLY_NO_EDITS = False
    clear_active_step_trace()
    start_step_trace("T-NOWRITE", "No write", "Developer", "In Progress")
    get_active_trace().log_tool("apply_patch", False, "context does not match")
    reason = derive_exit_reason(
        agent_result="Max tool iterations (6) reached",
        tools_used={"apply_patch"},
        lane_before="In Progress",
        lane_after="In Progress",
    )
    assert reason == "max_iterations"
    clear_active_step_trace()


def test_identical_write_loop_exit_reason():
    assert (
        derive_exit_reason(
            agent_result="Stopped: identical write loop — the same successful patch repeated on lib/a.dart.",
            tools_used={"apply_patch"},
            lane_before="In Progress",
            lane_after="In Progress",
        )
        == "identical_write_loop"
    )


def test_write_dup_skip_exit_reason_is_max_iterations_after_writes():
    assert (
        derive_exit_reason(
            agent_result=(
                "Stopped: files already written this step and verify command was a duplicate skip. "
                "Continuing to lint/lane advance."
            ),
            tools_used={"write_file", "run_command"},
            lane_before="In Progress",
            lane_after="In Progress",
        )
        == "max_iterations_after_writes"
    )


def test_po_lane_after_tool_not_finalize_lane(tmp_path, monkeypatch):
    monkeypatch.setenv("ALLHANDS_HOME", str(tmp_path))
    initialize()
    state.CURRENT_PROJECT_ID = "test-proj"
    clear_active_step_trace()
    trace = start_step_trace("T-PO", "Clarify", "Product Owner", "Needs PO")
    record_sampling_snapshot({"model": "gemma", "num_predict": 2048, "provider": "ollama"})
    trace.log_tool("update_board", True, "TASK-T-PO → In Progress")
    record_po_json_applied(True)
    trace.log_event("po_num_predict_bump", "num_predict=4096")
    state.LAST_STEP_OUTCOME = {"ok": False, "exitReason": "tool_failure_stop", "taskId": "T-PO"}
    summary = finalize_active_step_trace(lane_after="Done", agent_result="clarified")
    data = json.loads(Path(summary["filePath"]).read_text(encoding="utf-8"))
    assert data["laneAfterTool"] == "In Progress"
    assert data["laneAfter"] == "Done"
    assert data["poJsonApplied"] is True
    assert data["poNumPredictBumped"] is True
    assert data["sampling"]["num_predict"] == 2048


def test_log_ollama_call_emits_ctx_truncated_and_empty_gen(tmp_path, monkeypatch):
    monkeypatch.setenv("ALLHANDS_HOME", str(tmp_path))
    initialize()
    state.CURRENT_PROJECT_ID = "test-proj"
    clear_active_step_trace()
    trace = start_step_trace("T-CTX", "Ctx", "Developer", "In Progress")
    log_ollama_call(
        1,
        duration_ms=2500,
        eval_tokens=2048,
        num_ctx=5120,
        done_reason="length",
    )
    log_ollama_call(
        2,
        duration_ms=91000,
        eval_tokens=0,
        error="Ollama empty generation timed out after 90s",
        error_type="empty_generation_timeout",
    )
    kinds = [e["kind"] for e in trace.events]
    assert "ctx_truncated" in kinds
    assert "empty_generation_timeout" in kinds
    empty = next(e for e in trace.events if e["kind"] == "empty_generation_timeout")
    assert "elapsed_ms=91000" in empty["message"]
    assert "eval_count=0" in empty["message"]
    clear_active_step_trace()


def test_classify_tool_failure_helpers():
    assert classify_tool_failure("apply_patch", "old_text mismatch") == "patch_mismatch"
    assert classify_tool_failure("run_command", "flutter pub get") == "command_nonzero"
    assert classify_tool_failure("read_file", "path not found") == "path_missing"
    assert classify_tool_failure("update_board", "blocked") == "board_blocked"


def test_ollama_wait_heartbeat_includes_elapsed(tmp_path, monkeypatch):
    monkeypatch.setenv("ALLHANDS_HOME", str(tmp_path))
    initialize()
    state.CURRENT_PROJECT_ID = "test-proj"
    msg0 = format_ollama_wait_event(
        iteration=2, max_iterations=6, model="gemma-4-q4km:26b", elapsed_sec=0
    )
    msg1 = format_ollama_wait_event(
        iteration=2,
        max_iterations=6,
        model="gemma-4-q4km:26b",
        elapsed_sec=45,
        last_tool="write_file",
    )
    assert "elapsed=0s" in msg0
    assert "elapsed=45s" in msg1
    assert "last_tool=write_file" in msg1
    assert msg0 != msg1
    trace = start_step_trace("T-WAIT", "Wait", "Developer", "In Progress")
    log_event("ollama_wait", msg0)
    assert trace.last_event.startswith("ollama_wait:iter 2/6 elapsed=0s")
    log_event("ollama_wait", msg1)
    assert "elapsed=45s" in trace.last_event
    assert "write_file" in trace.last_event
    summary = finalize_active_step_trace(lane_after="In Progress")
    kinds = [e.get("kind") for e in (summary or {}).get("events") or []]
    assert kinds.count("ollama_wait") >= 2


def test_format_console_ollama_wait_includes_elapsed_and_last_tool():
    msg = format_console_ollama_wait(
        elapsed_sec=45,
        iteration=2,
        max_iterations=6,
        last_tool="write_file",
    )
    assert "Still waiting for Ollama" in msg
    assert "45s" in msg
    assert "iter 2/6" in msg
    assert "last_tool=write_file" in msg


def test_derive_exit_reason_lint_recovery_message():
    msg = "Lint/tool errors remain — staying In Progress so Developer can patch. Not moving to Needs User."
    assert derive_exit_reason(
        agent_result=msg,
        tools_used=set(),
        lane_before="In Progress",
        lane_after="In Progress",
    ) == "lint_stay_in_progress"


def test_derive_exit_reason_patch_fail_on_lint_card(tmp_path, monkeypatch):
    monkeypatch.setenv("ALLHANDS_HOME", str(tmp_path))
    initialize()
    task = init_new_task(
        {
            "id": "T-PATCH-FAIL",
            "title": "Lint: lib/main.dart",
            "description": "d",
            "status": "In Progress",
            "lintSourceFile": "lib/main.dart",
            "lastCommandDiagnostics": [
                {"file": "lib/main.dart", "line": 10, "message": "override error", "severity": "warning"}
            ],
        }
    )
    state.SHARED_BOARD["In Progress"] = [task]
    trace = start_step_trace("T-PATCH-FAIL", "Lint: lib/main.dart", "Developer", "In Progress")
    trace.log_tool("write_file", True, "lib/main.dart (349 chars)")
    trace.log_tool("apply_patch", False, "lib/main.dart (replace 37 chars)")
    reason = derive_exit_reason(
        agent_result="done",
        tools_used={"write_file", "apply_patch"},
        lane_before="In Progress",
        lane_after="In Progress",
    )
    assert reason == "tool_failure_stop"
    clear_active_step_trace()


def test_lint_recovery_does_not_overwrite_dev_outcome():
    from backend import state
    from backend.agents.task_context import init_new_task
    from backend.bootstrap import initialize
    from backend.services.sprint_service import (
        _outcome_suggested_action,
        _outcome_why_card_stayed,
        _recover_latched_dev_card,
    )

    initialize()
    state.SHARED_BOARD.clear()
    for lane in ("In Progress", "Blocked", "Done"):
        state.SHARED_BOARD[lane] = []
    task = init_new_task(
        {
            "id": "T-KEEP-OUT",
            "title": "Lint: lib/main.dart",
            "description": "d",
            "status": "In Progress",
            "lintSourceFile": "lib/main.dart",
            "forcePatchNextDevStep": True,
            "lastCommandDiagnostics": [
                {"file": "lib/main.dart", "line": 1, "message": "err", "severity": "error"}
            ],
            "lastStepOutcome": {
                "agent": "Developer",
                "exitReason": "tool_failure_stop",
                "whyCardStayed": "apply_patch failed",
                "suggestedAction": "retry",
            },
        }
    )
    state.SHARED_BOARD["In Progress"] = [task]
    _recover_latched_dev_card(dict(task), "", quiet=False)
    live = state.SHARED_BOARD["In Progress"][0]
    assert live["lastStepOutcome"]["agent"] == "Developer"
    assert live["lastStepOutcome"]["exitReason"] == "tool_failure_stop"


def test_lint_outcome_copy_mentions_forced_patch_retry():
    from backend.services.sprint_service import _outcome_suggested_action, _outcome_why_card_stayed

    task = {
        "lintSourceFile": "lib/main.dart",
        "lastCommandDiagnostics": [{"file": "lib/main.dart", "line": 1, "message": "x"}],
    }
    why = _outcome_why_card_stayed(
        "lint_stay_in_progress",
        title="Lint: lib/main.dart",
        lane_after="In Progress",
        task=task,
    )
    action = _outcome_suggested_action(
        "tool_failure_stop",
        "In Progress",
        task={**task, "forcePatchNextDevStep": True},
    )
    assert "text-only" not in why.lower()
    assert "forced patch" in why.lower()
    assert "retry" in action.lower()
    assert "manual" not in action.lower()


def test_force_patch_instruction_includes_last_patch_error():
    from backend.services.sprint_service import _force_patch_dev_instruction

    task = {
        "title": "Fix main",
        "forcePatchNextDevStep": True,
        "identicalPatchFailCount": 2,
        "transcript": [
            {
                "toolName": "apply_patch",
                "toolSuccess": False,
                "content": "Error: old_text not found on lib/main.dart",
            }
        ],
    }
    instr = _force_patch_dev_instruction(task, "Developer")
    assert "FORCED PATCH" in instr
    assert "old_text not found" in instr
    assert "write_file" in instr.lower()


def test_force_patch_instruction_includes_lint_diagnostic():
    from backend.services.sprint_service import _force_patch_dev_instruction

    task = {
        "title": "Lint: lib/main.dart",
        "lintSourceFile": "lib/main.dart",
        "forcePatchNextDevStep": True,
        "lastCommandDiagnostics": [
            {
                "file": "lib/main.dart",
                "line": 42,
                "message": "The method doesn't override an inherited method",
                "severity": "warning",
            }
        ],
    }
    instr = _force_patch_dev_instruction(task, "Developer")
    assert "FORCED PATCH" in instr
    assert "lib/main.dart" in instr
    assert "override" in instr.lower()


def test_record_dev_precheck_skip_writes_diagnostics(tmp_path, monkeypatch):
    monkeypatch.setenv("ALLHANDS_HOME", str(tmp_path))
    initialize()
    state.CURRENT_PROJECT_ID = "test-proj"
    state.SPRINT_PROGRESS_MAX = 20
    state.LAST_STEP_DIAGNOSTICS = None
    clear_active_step_trace()

    park_msg = (
        "Same next task reissued with no writes or better oracle — parking instead of another generate."
    )
    from backend.services.sprint_service import _record_dev_precheck_skip

    _record_dev_precheck_skip(
        "T-PRECHECK",
        "Lint: lib/main.dart",
        "In Progress",
        reason=park_msg,
    )

    assert state.LAST_STEP_DIAGNOSTICS is not None
    path = Path(state.LAST_STEP_DIAGNOSTICS["filePath"])
    assert path.is_file()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["taskId"] == "T-PRECHECK"
    assert data["status"] == "complete"
    assert data["exitReason"] == "dev_precheck_skip"
    assert park_msg[:40] in data["agentResultSnippet"]
    assert get_active_trace() is None


def test_record_dev_precheck_skip_when_sprint_max_one(tmp_path, monkeypatch):
    monkeypatch.setenv("ALLHANDS_HOME", str(tmp_path))
    initialize()
    state.CURRENT_PROJECT_ID = "test-proj"
    state.SPRINT_PROGRESS_MAX = 1
    clear_active_step_trace()

    from backend.services.sprint_service import _record_dev_precheck_skip

    _record_dev_precheck_skip(
        "T-PRECHECK-1",
        "Lint: lib/main.dart",
        "In Progress",
        reason="Lint stall — Forced Patch retry queued",
    )

    assert state.LAST_STEP_DIAGNOSTICS is not None
    assert Path(state.LAST_STEP_DIAGNOSTICS["filePath"]).is_file()
    assert get_active_trace() is None


def test_empty_trace_does_not_embed_stale_step_progress(tmp_path, monkeypatch):
    monkeypatch.setenv("ALLHANDS_HOME", str(tmp_path))
    initialize()
    state.CURRENT_PROJECT_ID = "test-proj"
    state.LAST_STEP_PROGRESS = {
        "taskId": "T-STALE",
        "intent": "Waiting for model (Ollama) — iter 6/30 · 915s — LLM call in flight",
    }
    trace = start_step_trace("T-STALE", "Feature work", "Developer", "In Progress")
    summary = finalize_active_step_trace(
        lane_after="In Progress",
        agent_result="Same next task reissued with no writes or better oracle — parking instead of another generate.",
    )
    assert summary is not None
    data = json.loads(trace.file_path.read_text(encoding="utf-8"))
    assert data["exitReason"] == "dev_precheck_skip"
    assert "stepProgress" not in data


def test_record_dev_precheck_skip_includes_park_fields(tmp_path, monkeypatch):
    monkeypatch.setenv("ALLHANDS_HOME", str(tmp_path))
    initialize()
    state.CURRENT_PROJECT_ID = "test-proj"
    state.SPRINT_PROGRESS_MAX = 20
    clear_active_step_trace()

    from backend.services.sprint_service import _record_dev_precheck_skip

    _record_dev_precheck_skip(
        "T-PARK-FIELDS",
        "Import card",
        "In Progress",
        reason="Same next task reissued with no writes or better oracle — parking instead of another generate.",
        park_attempted=True,
        park_succeeded=False,
        needs_user_kind="phase_cycle_cap",
    )

    data = json.loads(Path(state.LAST_STEP_DIAGNOSTICS["filePath"]).read_text(encoding="utf-8"))
    assert data["parkAttempted"] is True
    assert data["parkSucceeded"] is False
    assert data["needsUserKind"] == "phase_cycle_cap"

