from fastapi import APIRouter

from backend import state
from backend.api.schemas import TerminalPayload
from backend.services.terminal_service import run_command

router = APIRouter()


@router.post("/api/terminal/run")
def terminal_run(payload: TerminalPayload):
    with state.STATE_LOCK:
        result = run_command(payload.command)
        stdout = result.get("stdout") or ""
        stderr = result.get("stderr") or ""
        output = "\n".join(part for part in (stdout, stderr) if part).strip()
        returncode = result.get("returncode", -1)
        return {
            "output": output or "(no output)",
            "exitCode": returncode,
            "success": result.get("success", False),
            "stdout": stdout,
            "stderr": stderr,
            "returncode": returncode,
        }
