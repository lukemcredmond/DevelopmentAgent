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
from backend.services.tool_approval import list_pending_approvals, resolve_tool_approval

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


class ToolApprovalPayload(BaseModel):
    approved: bool


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


@router.get("/api/tools/pending-approvals")
def get_pending_approvals():
    with state.STATE_LOCK:
        return {"pending": list_pending_approvals()}


@router.post("/api/tools/approvals/{approval_id}")
def post_tool_approval(approval_id: str, payload: ToolApprovalPayload):
    with state.STATE_LOCK:
        ok = resolve_tool_approval(approval_id, payload.approved)
        if not ok:
            raise HTTPException(status_code=404, detail="Approval request not found or already resolved")
    return {"ok": True, "pending": list_pending_approvals()}
