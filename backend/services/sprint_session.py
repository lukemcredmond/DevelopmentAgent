"""Persisted sprint session checkpoint for crash recovery."""

from __future__ import annotations

import json
import time
from datetime import datetime
from typing import Any, Dict, Literal, Optional

from backend import state
from backend.services.project_service import save_current_project_state

SESSION_KEY_PREFIX = "sprint_session:"
TOUCH_INTERVAL_SEC = 12.0

SprintMode = Literal["auto", "single_step", "in_progress"]
SessionStatus = Literal["running", "idle", "interrupted"]

_last_touch_monotonic: float = 0.0
_current_sprint_mode: SprintMode = "single_step"


def _session_key(project_id: str) -> str:
    return f"{SESSION_KEY_PREFIX}{project_id}"


def _now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _load_session(project_id: str | None = None) -> Optional[Dict[str, Any]]:
    pid = project_id or state.CURRENT_PROJECT_ID
    raw = state.storage.get_setting(_session_key(pid))
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def _save_session(session: Dict[str, Any], project_id: str | None = None) -> None:
    pid = project_id or state.CURRENT_PROJECT_ID
    state.storage.set_setting(_session_key(pid), json.dumps(session))


def set_sprint_mode(mode: SprintMode) -> None:
    global _current_sprint_mode
    _current_sprint_mode = mode


def start_session(
    *,
    task_id: str,
    task_title: str,
    lane: str,
    agent: str,
    handler: str,
    sprint_mode: Optional[SprintMode] = None,
    diagnostics_file: Optional[str] = None,
) -> None:
    global _last_touch_monotonic
    mode = sprint_mode or _current_sprint_mode
    now = _now_str()
    session = {
        "status": "running",
        "taskId": task_id,
        "taskTitle": task_title,
        "lane": lane,
        "agent": agent,
        "handler": handler,
        "startedAt": now,
        "updatedAt": now,
        "sprintMode": mode,
        "diagnosticsFile": diagnostics_file or "",
        "stepStartedAt": now,
        "lastEvent": "",
    }
    _save_session(session)
    _last_touch_monotonic = time.monotonic()
    with state.STATE_LOCK:
        save_current_project_state()


def touch_session(
    *,
    last_event: Optional[str] = None,
    diagnostics_file: Optional[str] = None,
    force: bool = False,
) -> None:
    global _last_touch_monotonic
    session = _load_session()
    if not session or session.get("status") != "running":
        return
    now_mono = time.monotonic()
    if not force and (now_mono - _last_touch_monotonic) < TOUCH_INTERVAL_SEC:
        return
    _last_touch_monotonic = now_mono
    session["updatedAt"] = _now_str()
    if last_event:
        session["lastEvent"] = last_event
    if diagnostics_file:
        session["diagnosticsFile"] = diagnostics_file
    _save_session(session)
    with state.STATE_LOCK:
        save_current_project_state()


def clear_session(status: SessionStatus = "idle") -> None:
    session = _load_session()
    if session:
        session["status"] = status
        session["updatedAt"] = _now_str()
        _save_session(session)
    else:
        _save_session({"status": status, "updatedAt": _now_str()})


def mark_interrupted(project_id: str | None = None) -> None:
    session = _load_session(project_id)
    if session and session.get("status") == "running":
        session["status"] = "interrupted"
        session["updatedAt"] = _now_str()
        _save_session(session, project_id)


def dismiss_interrupted() -> None:
    session = _load_session()
    if session and session.get("status") == "interrupted":
        session["status"] = "idle"
        session["updatedAt"] = _now_str()
        _save_session(session)
    state.RECOVERY_CONTEXT = None


def get_recovery_context() -> Optional[Dict[str, Any]]:
    if state.RECOVERY_CONTEXT is not None:
        return state.RECOVERY_CONTEXT
    session = _load_session()
    if not session or session.get("status") != "interrupted":
        return None
    return _build_recovery_from_session(session)


def _build_recovery_from_session(session: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "interrupted": True,
        "taskId": session.get("taskId", ""),
        "taskTitle": session.get("taskTitle", ""),
        "lane": session.get("lane", ""),
        "agent": session.get("agent", ""),
        "diagnosticsFile": session.get("diagnosticsFile", ""),
        "lastEvent": session.get("lastEvent", ""),
        "suggestedAction": "Run In Progress on this card",
    }


def detect_recovery_on_startup(project_id: str | None = None) -> Optional[Dict[str, Any]]:
    """After loading a project, mark running sessions interrupted and build recovery context."""
    from backend.services.step_diagnostics import finalize_orphaned_diagnostics

    pid = project_id or state.CURRENT_PROJECT_ID
    session = _load_session(pid)
    if not session:
        state.RECOVERY_CONTEXT = None
        return None

    if session.get("status") == "running":
        session["status"] = "interrupted"
        session["updatedAt"] = _now_str()
        _save_session(session, pid)

    if session.get("status") != "interrupted":
        state.RECOVERY_CONTEXT = None
        return None

    task_id = str(session.get("taskId", ""))
    diag_file = str(session.get("diagnosticsFile", ""))
    last_event = str(session.get("lastEvent", ""))

    orphaned = finalize_orphaned_diagnostics(task_id=task_id, diagnostics_path=diag_file or None)
    if orphaned:
        if orphaned.get("lastEvent"):
            last_event = str(orphaned["lastEvent"])
        if orphaned.get("filePath"):
            diag_file = str(orphaned["filePath"])
        session["diagnosticsFile"] = diag_file
        session["lastEvent"] = last_event
        _save_session(session, pid)

    recovery = _build_recovery_from_session(session)
    recovery["diagnosticsFile"] = diag_file
    recovery["lastEvent"] = last_event
    state.RECOVERY_CONTEXT = recovery
    return recovery
