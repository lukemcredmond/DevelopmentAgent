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
    finalize_active_step_trace,
    get_active_trace,
    start_step_trace,
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
    assert data["exitReason"] == "read_only_no_edits"
    assert data["ollamaCalls"][0]["toolCalls"] == ["read_file"]
    assert data["taskId"] == "T-1"
    assert "filePath" in data
    assert get_active_trace() is None
    assert state.LAST_STEP_DIAGNOSTICS is not None


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
