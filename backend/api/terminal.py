from fastapi import APIRouter

from backend import state
from backend.api.schemas import TerminalPayload
from backend.services.terminal_service import run_command

router = APIRouter()


@router.post("/api/terminal/run")
def terminal_run(payload: TerminalPayload):
    with state.STATE_LOCK:
        result = run_command(payload.command)
        return result
