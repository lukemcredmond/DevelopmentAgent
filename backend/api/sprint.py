from fastapi import APIRouter

from backend import state
from backend.api.helpers import build_state_response
from backend.api.schemas import BriefPayload, PlanBacklogPayload, SprintRunPayload, WorkflowSettingsPayload
from backend.services.board_lanes import normalize_board_lanes
from backend.services.sprint_service import (
    run_auto_sprint,
    run_plan_and_run,
    run_po_plan,
    run_po_plan_backlog,
    run_po_plan_outline,
    run_sprint_step,
)
from backend.services.workflow_settings import get_workflow_settings, save_workflow_settings

router = APIRouter()


@router.post("/api/plan")
def trigger_po_plan(payload: BriefPayload):
    with state.STATE_LOCK:
        run_po_plan(payload.brief, payload.ollama_url)
    return build_state_response()


@router.post("/api/plan/outline")
def trigger_po_plan_outline(payload: BriefPayload):
    with state.STATE_LOCK:
        outline = run_po_plan_outline(payload.brief, payload.ollama_url)
    response = build_state_response()
    response["projectPlanOutline"] = outline
    return response


@router.post("/api/plan/backlog")
def trigger_po_plan_backlog(payload: PlanBacklogPayload):
    with state.STATE_LOCK:
        run_po_plan_backlog(payload.brief, payload.ollama_url, outline=payload.outline)
    return build_state_response()


@router.post("/api/step")
def trigger_agent_turn(payload: BriefPayload):
    with state.STATE_LOCK:
        run_sprint_step(payload.brief, payload.ollama_url)
    return build_state_response()


@router.post("/api/sprint/run")
def trigger_auto_sprint(payload: SprintRunPayload):
    run_auto_sprint(payload.brief, payload.ollama_url, max_steps=payload.max_steps)
    with state.STATE_LOCK:
        return build_state_response()


@router.post("/api/sprint/plan-and-run")
def trigger_plan_and_run(payload: SprintRunPayload):
    run_plan_and_run(payload.brief, payload.ollama_url, max_steps=payload.max_steps)
    with state.STATE_LOCK:
        return build_state_response()


@router.post("/api/sprint/cancel")
def cancel_auto_sprint():
    state.SPRINT_CANCEL = True
    return {"ok": True, "sprintCancel": True}


@router.post("/api/workflow/settings")
def update_workflow_settings(payload: WorkflowSettingsPayload):
    with state.STATE_LOCK:
        updates = payload.model_dump(exclude_none=True)
        saved = save_workflow_settings(updates)
        normalize_board_lanes(state.SHARED_BOARD)
        from backend.agents.registry import configure_agent_tools

        configure_agent_tools(saved)
    return build_state_response()


@router.get("/api/workflow/settings")
def get_workflow_settings_route():
    return {"workflowSettings": get_workflow_settings()}
