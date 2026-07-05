from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from backend import state
from backend.api.helpers import build_state_response
from backend.services.tool_aliases import (
    delete_alias,
    get_aliases,
    list_pending_tools,
    resolve_pending_tool,
    save_alias,
)

router = APIRouter()


class ToolAliasPayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    alias: str
    target_tool: str = Field(alias="targetTool")
    default_args: Optional[Dict[str, Any]] = Field(default=None, alias="defaultArgs")


class ResolvePendingPayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    target_tool: str = Field(alias="targetTool")
    default_args: Optional[Dict[str, Any]] = Field(default=None, alias="defaultArgs")
    save_mapping: bool = Field(default=True, alias="saveMapping")


@router.get("/api/tools/pending")
def get_pending_tools():
    with state.STATE_LOCK:
        return {"pending": list_pending_tools()}


@router.get("/api/tools/aliases")
def get_tool_aliases():
    with state.STATE_LOCK:
        return {"aliases": get_aliases()}


@router.post("/api/tools/aliases")
def post_tool_alias(payload: ToolAliasPayload):
    with state.STATE_LOCK:
        save_alias(payload.alias, payload.target_tool, payload.default_args or {})
    return build_state_response()


@router.delete("/api/tools/aliases/{alias}")
def remove_tool_alias(alias: str):
    with state.STATE_LOCK:
        delete_alias(alias)
    return build_state_response()


@router.post("/api/tools/pending/{request_id}/resolve")
def resolve_pending(request_id: str, payload: ResolvePendingPayload):
    with state.STATE_LOCK:
        result = resolve_pending_tool(
            request_id,
            payload.target_tool,
            payload.default_args,
            save_mapping=payload.save_mapping,
        )
        if not result:
            raise HTTPException(status_code=404, detail="Pending tool request not found")
    return {"ok": True, "mapping": result, "pending": list_pending_tools()}
