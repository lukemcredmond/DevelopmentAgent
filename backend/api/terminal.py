from fastapi import APIRouter

from backend import state
from backend.api.schemas import TerminalPayload
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
