from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend import state
from backend.api.helpers import build_state_response
from backend.services.prompt_retry import extend_agent_step, retry_agent_step

router = APIRouter()


class RetryStepPayload(BaseModel):
    taskId: str
    agentId: str = "dev"
    mode: str = "same"
    ollamaUrl: str = "http://localhost:11434"
    reason: str = "user_requested"
    allowDoneRetry: bool = False


class ExtendStepPayload(BaseModel):
    taskId: str
    agentId: str = "dev"
    action: str = "extend"  # extend | reset
    extraIterations: int = 4
    ollamaUrl: str = "http://localhost:11434"
    allowDoneRetry: bool = False


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
            allow_done_retry=payload.allowDoneRetry,
        )
        if not result.get("ok") and result.get("error"):
            raise HTTPException(status_code=400, detail=result["error"])
    return {**result, "state": build_state_response()}


@router.post("/api/agents/extend-step")
def post_extend_step(payload: ExtendStepPayload):
    action = (payload.action or "extend").strip().lower()
    if action not in ("extend", "reset"):
        raise HTTPException(status_code=400, detail="action must be 'extend' or 'reset'")
    with state.STATE_LOCK:
        result = extend_agent_step(
            payload.taskId,
            payload.agentId,
            payload.ollamaUrl,
            action=action,
            extra_iterations=payload.extraIterations,
            brief=state.PROJECT_BRIEF,
            allow_done_retry=payload.allowDoneRetry,
        )
        if not result.get("ok") and result.get("error"):
            raise HTTPException(status_code=400, detail=result["error"])
    return {**result, "state": build_state_response()}
