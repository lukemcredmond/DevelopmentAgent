from fastapi import APIRouter, HTTPException

from backend import state
from backend.api.helpers import build_state_response
from backend.api.schemas import TerminalPayload
from backend.services.background_terminal import (
    list_sessions,
    read_session_output,
    start_background_command,
    stop_session,
)
from backend.services.command_result import format_command_result_for_agent, run_workspace_command

router = APIRouter()


@router.post("/api/terminal/run")
def terminal_run(payload: TerminalPayload):
    with state.STATE_LOCK:
        result = run_workspace_command(payload.command)
        formatted = format_command_result_for_agent(result)
        return {
            "output": formatted,
            "exitCode": result.exit_code,
            "success": result.outcome == "ok",
            "stdout": result.stdout,
            "stderr": result.stderr,
            "returncode": result.exit_code,
            "outcome": result.outcome,
            "summary": result.summary,
            "diagnostics": result.diagnostics,
            "diagnosticsCount": len(result.diagnostics),
        }


@router.post("/api/terminal/background")
def terminal_background_start(payload: TerminalPayload):
    ok, msg, session_id = start_background_command(payload.command)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    return {"sessionId": session_id, "command": payload.command}


@router.get("/api/terminal/background")
def terminal_background_list():
    return {"sessions": list_sessions()}


@router.get("/api/terminal/background/{session_id}")
def terminal_background_output(session_id: str, offset: int = 0):
    data = read_session_output(session_id, offset=offset)
    if data.get("error"):
        raise HTTPException(status_code=404, detail=data["error"])
    return data


@router.delete("/api/terminal/background/{session_id}")
def terminal_background_stop(session_id: str):
    if not stop_session(session_id):
        raise HTTPException(status_code=404, detail="Session not found")
    return {"ok": True}
