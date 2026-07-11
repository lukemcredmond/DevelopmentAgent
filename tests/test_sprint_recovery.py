"""Sprint session checkpoint and crash recovery on startup."""

import json
import time
from unittest.mock import patch

from backend import state
from backend.agents.task_context import init_new_task, record_task_decision
from backend.bootstrap import initialize, load_project_into_state
from backend.services.sprint_service import run_in_progress_step
from backend.services.sprint_session import (
    SESSION_KEY_PREFIX,
    TOUCH_INTERVAL_SEC,
    clear_session,
    detect_recovery_on_startup,
    dismiss_interrupted,
    get_recovery_context,
    start_session,
    touch_session,
)
from backend.services.step_diagnostics import start_step_trace


def _session_key() -> str:
    return f"{SESSION_KEY_PREFIX}{state.CURRENT_PROJECT_ID}"


def test_session_saved_on_step_start_cleared_on_end(tmp_path, monkeypatch):
    monkeypatch.setenv("ALLHANDS_HOME", str(tmp_path))
    initialize()
    state.SHARED_BOARD.clear()
    for lane in ("Backlog", "In Progress", "Needs User", "Needs PO", "QA", "Done", "Refinement", "Code Review"):
        state.SHARED_BOARD[lane] = []

    task = init_new_task({"id": "T-REC", "title": "Recovery task", "description": "d", "status": "In Progress"})
    state.SHARED_BOARD["In Progress"] = [task]

    def fake_dev_step(active_task, *_args, **_kwargs):
        raw = state.storage.get_setting(_session_key())
        assert raw is not None
        session = json.loads(raw)
        assert session["status"] == "running"
        assert session["taskId"] == "T-REC"
        assert session["sprintMode"] == "in_progress"

    with patch("backend.services.sprint_service._run_developer_step", side_effect=fake_dev_step):
        run_in_progress_step("brief", "http://localhost:11434")

    raw = state.storage.get_setting(_session_key())
    assert raw is not None
    session = json.loads(raw)
    assert session["status"] == "idle"


def test_startup_running_session_becomes_interrupted_with_recovery(tmp_path, monkeypatch):
    monkeypatch.setenv("ALLHANDS_HOME", str(tmp_path))
    initialize()

    start_session(
        task_id="T-CRASH",
        task_title="Crashed task",
        lane="In Progress",
        agent="Developer",
        handler="dev",
        sprint_mode="in_progress",
        diagnostics_file="",
    )
    raw = state.storage.get_setting(_session_key())
    session = json.loads(raw)
    session["status"] = "running"
    state.storage.set_setting(_session_key(), json.dumps(session))

    recovery = detect_recovery_on_startup()
    assert recovery is not None
    assert recovery["interrupted"] is True
    assert recovery["taskId"] == "T-CRASH"
    assert recovery["taskTitle"] == "Crashed task"
    assert get_recovery_context() is not None

    raw = state.storage.get_setting(_session_key())
    assert json.loads(raw)["status"] == "interrupted"


def test_dismiss_clears_recovery(tmp_path, monkeypatch):
    monkeypatch.setenv("ALLHANDS_HOME", str(tmp_path))
    initialize()

    start_session(
        task_id="T-DIS",
        task_title="Dismiss me",
        lane="In Progress",
        agent="Developer",
        handler="dev",
    )
    session = json.loads(state.storage.get_setting(_session_key()))
    session["status"] = "interrupted"
    state.storage.set_setting(_session_key(), json.dumps(session))
    state.RECOVERY_CONTEXT = get_recovery_context()

    dismiss_interrupted()
    assert get_recovery_context() is None
    assert json.loads(state.storage.get_setting(_session_key()))["status"] == "idle"


def test_throttled_touch_persists_board_mid_step(tmp_path, monkeypatch):
    monkeypatch.setenv("ALLHANDS_HOME", str(tmp_path))
    initialize()
    state.SHARED_BOARD.clear()
    for lane in ("Backlog", "In Progress", "Needs User", "Needs PO", "QA", "Done", "Refinement", "Code Review"):
        state.SHARED_BOARD[lane] = []

    task = init_new_task({"id": "T-TOUCH", "title": "Touch task", "description": "d", "status": "In Progress"})
    state.SHARED_BOARD["In Progress"] = [task]
    start_session(
        task_id="T-TOUCH",
        task_title="Touch task",
        lane="In Progress",
        agent="Developer",
        handler="dev",
    )

    record_task_decision("T-TOUCH", "Developer", "note", "mid-step note")
    touch_calls: list[float] = []

    def fake_save():
        touch_calls.append(time.monotonic())

    with patch("backend.services.sprint_session.save_current_project_state", side_effect=fake_save):
        touch_session(last_event="tool:read_file")
        touch_session(last_event="tool:read_file_again")
        touch_session(last_event="tool:apply_patch", force=True)

    assert len(touch_calls) == 1

    with patch("backend.services.sprint_session.save_current_project_state", side_effect=fake_save):
        touch_session(last_event="first")
        time.sleep(TOUCH_INTERVAL_SEC + 0.05)
        touch_session(last_event="second")

    assert len(touch_calls) == 2


def test_orphaned_diagnostics_finalized_on_startup(tmp_path, monkeypatch):
    monkeypatch.setenv("ALLHANDS_HOME", str(tmp_path))
    initialize()
    state.CURRENT_PROJECT_ID = "test-proj"

    trace = start_step_trace("T-DIAG", "Diag task", "Developer", "In Progress")
    trace.log_tool("read_file", True, "main.py")
    running_path = trace.file_path
    data = json.loads(running_path.read_text(encoding="utf-8"))
    assert data["status"] == "running"

    start_session(
        task_id="T-DIAG",
        task_title="Diag task",
        lane="In Progress",
        agent="Developer",
        handler="dev",
        diagnostics_file=str(running_path),
    )
    session = json.loads(state.storage.get_setting(_session_key()))
    session["status"] = "running"
    state.storage.set_setting(_session_key(), json.dumps(session))
    state.ACTIVE_STEP_DIAGNOSTICS = None

    recovery = detect_recovery_on_startup()
    assert recovery is not None
    assert recovery["diagnosticsFile"] == str(running_path)

    finalized = json.loads(running_path.read_text(encoding="utf-8"))
    assert finalized["status"] == "complete"
    assert finalized["exitReason"] == "interrupted"
    assert finalized["hint"] == "App restarted during this step"


def test_load_project_detects_recovery(tmp_path, monkeypatch):
    monkeypatch.setenv("ALLHANDS_HOME", str(tmp_path))
    initialize()
    project_id = state.CURRENT_PROJECT_ID

    start_session(
        task_id="T-LOAD",
        task_title="Load recovery",
        lane="In Progress",
        agent="Developer",
        handler="dev",
    )
    session = json.loads(state.storage.get_setting(_session_key()))
    session["status"] = "running"
    state.storage.set_setting(_session_key(), json.dumps(session))
    state.RECOVERY_CONTEXT = None

    assert load_project_into_state(project_id) is True
    assert state.RECOVERY_CONTEXT is not None
    assert state.RECOVERY_CONTEXT["taskId"] == "T-LOAD"


def test_mark_interrupted_on_shutdown_only_when_running(tmp_path, monkeypatch):
    monkeypatch.setenv("ALLHANDS_HOME", str(tmp_path))
    initialize()

    from backend.services.sprint_session import mark_interrupted

    clear_session("idle")
    mark_interrupted()
    assert json.loads(state.storage.get_setting(_session_key()))["status"] == "idle"

    start_session(
        task_id="T-SHUT",
        task_title="Shutdown",
        lane="In Progress",
        agent="Developer",
        handler="dev",
    )
    mark_interrupted()
    assert json.loads(state.storage.get_setting(_session_key()))["status"] == "interrupted"
