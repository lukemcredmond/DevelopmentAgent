from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from backend import state
from backend.api.helpers import build_state_response
from backend.services.events import event_stream

router = APIRouter()


@router.get("/api/state")
def get_state():
    with state.STATE_LOCK:
        return build_state_response()


@router.get("/api/events")
def get_events():
    return StreamingResponse(event_stream(), media_type="text/event-stream")
