"""Background terminal sessions with streamed output via SSE."""

from __future__ import annotations

import subprocess
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from backend import state
from backend.services.command_policy import validate_command
from backend.services.events import publish_event
from backend.services.logs import add_system_log


@dataclass
class BackgroundSession:
    id: str
    command: str
    started_at: str
    output: str = ""
    done: bool = False
    exit_code: Optional[int] = None
    error: Optional[str] = None
    _proc: Optional[subprocess.Popen] = field(default=None, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)


_SESSIONS: Dict[str, BackgroundSession] = {}
_LOCK = threading.Lock()


def list_sessions() -> List[Dict[str, Any]]:
    with _LOCK:
        return [
            {
                "id": s.id,
                "command": s.command,
                "startedAt": s.started_at,
                "done": s.done,
                "exitCode": s.exit_code,
                "outputLength": len(s.output),
            }
            for s in _SESSIONS.values()
        ]


def get_session(session_id: str) -> Optional[BackgroundSession]:
    return _SESSIONS.get(session_id)


def _reader_thread(session: BackgroundSession) -> None:
    proc = session._proc
    if not proc or not proc.stdout:
        return
    try:
        while True:
            chunk = proc.stdout.read(512)
            if not chunk:
                break
            with session._lock:
                session.output += chunk
            publish_event(
                "terminal_stream",
                {
                    "sessionId": session.id,
                    "chunk": chunk,
                    "outputLength": len(session.output),
                },
            )
    except Exception as exc:
        with session._lock:
            session.error = str(exc)
    finally:
        code = proc.wait() if proc else -1
        with session._lock:
            session.done = True
            session.exit_code = code
        publish_event(
            "terminal_stream",
            {
                "sessionId": session.id,
                "done": True,
                "exitCode": code,
                "outputLength": len(session.output),
            },
        )


def start_background_command(command: str) -> tuple[bool, str, Optional[str]]:
    ok, reason = validate_command(command)
    if not ok:
        return False, reason, None

    session_id = uuid.uuid4().hex[:12]
    started = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        proc = subprocess.Popen(
            command,
            shell=True,
            cwd=state.WORKSPACE_DIR or ".",
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except Exception as exc:
        return False, str(exc), None

    session = BackgroundSession(
        id=session_id,
        command=command,
        started_at=started,
        _proc=proc,
    )
    with _LOCK:
        _SESSIONS[session_id] = session
    threading.Thread(target=_reader_thread, args=(session,), daemon=True).start()
    add_system_log("System", "info", f"Background terminal started: {command[:80]}")
    publish_event(
        "terminal_stream",
        {"sessionId": session_id, "command": command, "started": True},
    )
    return True, "", session_id


def stop_session(session_id: str) -> bool:
    session = _SESSIONS.get(session_id)
    if not session or not session._proc:
        return False
    try:
        session._proc.terminate()
        session._proc.wait(timeout=3)
    except Exception:
        try:
            session._proc.kill()
        except Exception:
            pass
    with session._lock:
        session.done = True
    return True


def read_session_output(session_id: str, offset: int = 0) -> Dict[str, Any]:
    session = _SESSIONS.get(session_id)
    if not session:
        return {"error": "Session not found"}
    with session._lock:
        text = session.output[offset:]
        return {
            "sessionId": session_id,
            "chunk": text,
            "outputLength": len(session.output),
            "done": session.done,
            "exitCode": session.exit_code,
        }
