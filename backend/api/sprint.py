from fastapi import APIRouter, HTTPException

from backend import state
from backend.api.helpers import build_state_response
from backend.api.schemas import BriefPayload, PlanBacklogPayload, RunInProgressPayload, SprintRunPayload, WorkflowSettingsPayload
from pydantic import BaseModel, ConfigDict, Field
from backend.services.board_lanes import normalize_board_lanes
from backend.services.sprint_service import (
    run_auto_sprint,
    run_in_progress_step,
    run_plan_and_run,
    run_po_plan,
    run_po_plan_backlog,
    run_po_plan_outline,
    run_sprint_step,
)
from backend.services.workflow_settings import get_workflow_settings, save_workflow_settings

router = APIRouter()


class PhoneNotifyTestPayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    phone_notify_discord_webhook_url: str | None = Field(
        default=None, alias="phoneNotifyDiscordWebhookUrl"
    )


@router.post("/api/plan")
def trigger_po_plan(payload: BriefPayload):
    # Do not hold STATE_LOCK across Ollama — keeps board/settings/SSE responsive.
    run_po_plan(payload.brief, payload.ollama_url)
    return build_state_response()


@router.post("/api/plan/outline")
def trigger_po_plan_outline(payload: BriefPayload):
    outline = run_po_plan_outline(payload.brief, payload.ollama_url)
    response = build_state_response()
    response["projectPlanOutline"] = outline
    return response


@router.post("/api/plan/backlog")
def trigger_po_plan_backlog(payload: PlanBacklogPayload):
    run_po_plan_backlog(payload.brief, payload.ollama_url, outline=payload.outline)
    return build_state_response()


@router.post("/api/step")
def trigger_agent_turn(payload: BriefPayload):
    run_sprint_step(payload.brief, payload.ollama_url)
    return build_state_response()


@router.post("/api/sprint/run-in-progress")
def trigger_run_in_progress(payload: RunInProgressPayload):
    try:
        run_in_progress_step(payload.brief, payload.ollama_url, task_id=payload.task_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return build_state_response()


@router.get("/api/sprint/recovery")
def get_sprint_recovery():
    from backend.services.sprint_session import get_recovery_context

    return {"recovery": get_recovery_context()}


@router.post("/api/sprint/recovery/dismiss")
def dismiss_sprint_recovery():
    from backend.services.sprint_session import dismiss_interrupted

    dismiss_interrupted()
    return build_state_response()


@router.get("/api/sprint/diagnostics/latest")
def get_latest_step_diagnostics():
    if state.LAST_STEP_DIAGNOSTICS is None:
        raise HTTPException(status_code=404, detail="No step diagnostics available")
    return {"diagnostics": state.LAST_STEP_DIAGNOSTICS}


@router.post("/api/sprint/run")
def trigger_auto_sprint(payload: SprintRunPayload):
    run_auto_sprint(payload.brief, payload.ollama_url, max_steps=payload.max_steps)
    return build_state_response()


@router.post("/api/sprint/plan-and-run")
def trigger_plan_and_run(payload: SprintRunPayload):
    run_plan_and_run(payload.brief, payload.ollama_url, max_steps=payload.max_steps)
    return build_state_response()


@router.post("/api/sprint/cancel")
def cancel_auto_sprint():
    state.SPRINT_CANCEL = True
    state.SPRINT_CANCEL_INTENT = "cancelled"
    return {"ok": True, "sprintCancel": True, "sprintCancelIntent": "cancelled"}


@router.post("/api/workflow/settings")
def update_workflow_settings(payload: WorkflowSettingsPayload):
    with state.STATE_LOCK:
        updates = payload.model_dump(exclude_none=True)
        saved = save_workflow_settings(updates)
        normalize_board_lanes(state.SHARED_BOARD)
        from backend.agents.registry import configure_agent_tools

        configure_agent_tools(saved)
    # Reload optional Discord Gateway bot when enable/token/allowlist change.
    try:
        from backend.services.discord_bot import schedule_discord_bot_reload

        schedule_discord_bot_reload()
    except Exception:
        pass
    return build_state_response()


@router.get("/api/workflow/settings")
def get_workflow_settings_route():
    from backend.services.qdrant_auth import sanitize_workflow_settings_for_client

    return {"workflowSettings": sanitize_workflow_settings_for_client(get_workflow_settings())}


@router.post("/api/workflow/phone-notify/test")
def post_phone_notify_test(payload: PhoneNotifyTestPayload | None = None):
    """Send a test Discord webhook using current workflow settings (outbound only)."""
    from backend.services.phone_notify import send_test_notification

    override = None
    if payload is not None:
        override = payload.phone_notify_discord_webhook_url
    with state.STATE_LOCK:
        result = send_test_notification(
            webhook_url_override=str(override).strip() if override else None
        )
    if not result.get("ok") and result.get("error") == "invalid_webhook_url":
        raise HTTPException(status_code=400, detail="Invalid Discord webhook URL")
    if not result.get("ok") and result.get("skipped") == "missing_webhook":
        raise HTTPException(status_code=400, detail="Discord webhook URL not configured")
    if not result.get("ok") and result.get("skipped") == "disabled":
        raise HTTPException(status_code=400, detail="Phone notify is disabled")
    return result
