from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse

from backend import state
from backend.api.helpers import build_state_response
from backend.services.events import event_stream
from backend.services.logs import clear_system_logs

router = APIRouter()


@router.get("/api/state")
def get_state(include_files: bool = Query(True, alias="includeFiles")):
    with state.STATE_LOCK:
        return build_state_response(include_files=include_files)


@router.get("/api/events")
def get_events():
    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.post("/api/logs/clear")
def clear_logs():
    with state.STATE_LOCK:
        clear_system_logs()
        return {"ok": True, "logs": []}
