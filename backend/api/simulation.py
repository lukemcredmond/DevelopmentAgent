from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from backend import state
from backend.api.helpers import build_state_response
from backend.services.simulation_gate import (
    apply_simulation_confirmation,
    dismiss_pending_simulation,
    get_pending_simulation_public,
)

router = APIRouter()


class SimulationConfirmPayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    accept: bool = True
    override_target: str | None = Field(default=None, alias="overrideTarget")
    override_value: str | None = Field(default=None, alias="overrideValue")


@router.get("/api/simulation/pending")
def get_simulation_pending():
    with state.STATE_LOCK:
        pending = get_pending_simulation_public()
    return {"pendingSimulation": pending}


@router.post("/api/simulation/confirm")
def confirm_simulation(payload: SimulationConfirmPayload):
    with state.STATE_LOCK:
        result = apply_simulation_confirmation(
            accept=payload.accept,
            override_target=payload.override_target,
            override_value=payload.override_value,
        )
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error") or "Confirm failed")
    return build_state_response()


@router.post("/api/simulation/dismiss")
def dismiss_simulation():
    with state.STATE_LOCK:
        dismiss_pending_simulation()
    return build_state_response()
