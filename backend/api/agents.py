from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend import state
from backend.api.helpers import build_state_response
from backend.services.prompt_retry import retry_agent_step

router = APIRouter()


class RetryStepPayload(BaseModel):
    taskId: str
    agentId: str = "dev"
    mode: str = "same"
    ollamaUrl: str = "http://localhost:11434"
    reason: str = "user_requested"


@router.post("/api/agents/retry-step")
def post_retry_step(payload: RetryStepPayload):
    with state.STATE_LOCK:
        result = retry_agent_step(
            payload.taskId,
            payload.agentId,
            payload.ollamaUrl,
            mode=payload.mode,
            brief=state.PROJECT_BRIEF,
            reason=payload.reason,
        )
        if not result.get("ok") and result.get("error"):
            raise HTTPException(status_code=400, detail=result["error"])
    return {**result, "state": build_state_response()}
