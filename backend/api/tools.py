from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from backend import state
from backend.agents.registry import AGENT_MAP
from backend.api.helpers import build_state_response
from backend.services.tool_aliases import (
    delete_alias,
    get_aliases,
    list_pending_tools,
    resolve_pending_tool,
    save_alias,
)
from backend.services.tool_approval import list_pending_approvals, resolve_tool_approval
from backend.services.tool_execution_service import (
    clear_tool_log,
    execute_tool,
    get_tool_history,
    get_transcript_tool_entries,
    list_agent_tools,
    replay_transcript_tools,
)

router = APIRouter()

VALID_AGENTS = set(AGENT_MAP.keys())


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


class ToolExecutePayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    agent: str = "dev"
    tool_name: str = Field(alias="toolName")
    arguments: Dict[str, Any] = Field(default_factory=dict)
    task_id: Optional[str] = Field(default=None, alias="taskId")


class ToolReplayPayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    task_id: str = Field(alias="taskId")
    entry_indices: Optional[List[int]] = Field(default=None, alias="entryIndices")
    failed_only: bool = Field(default=False, alias="failedOnly")


def _tool_result_dict(result) -> Dict[str, Any]:
    return {
        "toolName": result.tool_name,
        "toolArgs": result.safe_args,
        "toolSuccess": result.success,
        "toolOutput": result.tool_output,
        "durationMs": result.duration_ms,
        "timestamp": result.timestamp,
        "agent": result.agent,
        "agentId": result.agent_id,
        "taskId": result.task_id,
        "source": result.source,
        "runId": result.run_id,
    }


@router.get("/api/tools/history")
def get_tools_history(limit: int = Query(default=200, ge=1, le=500)):
    with state.STATE_LOCK:
        return {"events": get_tool_history(limit=limit)}


@router.post("/api/tools/history/clear")
def clear_tools_history():
    with state.STATE_LOCK:
        return clear_tool_log()


@router.get("/api/tools/registry")
def get_tool_registry(agent: str = Query(default="dev")):
    if agent not in VALID_AGENTS:
        raise HTTPException(status_code=400, detail=f"Unknown agent: {agent}")
    with state.STATE_LOCK:
        return {"agent": agent, "tools": list_agent_tools(agent)}


@router.get("/api/tools/catalog")
def get_tools_catalog():
    """Builtin + custom tool definitions and per-agent effective tool lists."""
    from backend.agents.registry import (
        AGENT_LABELS,
        AGENT_MAP,
        BUILTIN_TOOL_CATALOG,
        configure_agent_tools,
    )
    from backend.services.custom_tools import QUERY_SQL_PRESET, list_custom_tool_defs
    from backend.services.workflow_settings import get_workflow_settings

    with state.STATE_LOCK:
        ws = get_workflow_settings()
        builtins = [
            {
                "name": name,
                "description": tool.description,
                "parameters": tool.parameters,
                "kind": "builtin",
            }
            for name, tool in sorted(BUILTIN_TOOL_CATALOG.items())
        ]
        customs = [{**d, "kind": "custom"} for d in list_custom_tool_defs(ws)]
        # Ensure registries reflect current settings
        configure_agent_tools(ws)
        agents_out = {}
        for key, agent in AGENT_MAP.items():
            role = AGENT_LABELS.get(key, agent.role)
            agents_out[role] = {
                "agentId": key,
                "tools": sorted(agent.registry.tool_names()),
            }
        return {
            "builtins": builtins,
            "customTools": customs,
            "agents": agents_out,
            "presets": {"query_sql": QUERY_SQL_PRESET},
            "agentTools": ws.get("agentTools") or {},
            "agentToolsAllowWritesInRefinement": bool(ws.get("agentToolsAllowWritesInRefinement")),
        }


@router.get("/api/tools/stack-catalog")
def get_stack_catalog(brief: int = Query(default=1, ge=0, le=1)):
    from backend.services.stack_catalog import build_stack_catalog

    with state.STATE_LOCK:
        return build_stack_catalog(use_brief=bool(brief))


@router.post("/api/tools/execute")
def post_tool_execute(payload: ToolExecutePayload):
    if payload.agent not in VALID_AGENTS:
        raise HTTPException(status_code=400, detail=f"Unknown agent: {payload.agent}")
    with state.STATE_LOCK:
        try:
            result = execute_tool(
                payload.agent,
                payload.tool_name,
                payload.arguments,
                task_id=payload.task_id,
                source="manual",
                skip_approval=True,
                user_prompt=f"Manual test: {payload.tool_name}",
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        from backend.services.project_service import save_current_project_state

        save_current_project_state()
    return {"ok": True, "result": _tool_result_dict(result)}


@router.get("/api/tools/transcript/{task_id}")
def get_task_tool_transcript(task_id: str):
    from backend.agents.task_context import find_task_by_id

    with state.STATE_LOCK:
        if not find_task_by_id(task_id):
            raise HTTPException(status_code=404, detail="Task not found")
        entries = get_transcript_tool_entries(task_id)
    return {"taskId": task_id, "entries": entries}


@router.post("/api/tools/replay")
def post_tool_replay(payload: ToolReplayPayload):
    with state.STATE_LOCK:
        try:
            results = replay_transcript_tools(
                payload.task_id,
                payload.entry_indices,
                failed_only=payload.failed_only,
            )
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        from backend.services.project_service import save_current_project_state

        save_current_project_state()
    return {
        "ok": True,
        "executed": len(results),
        "results": [_tool_result_dict(r) for r in results],
    }


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
        pending = list_pending_approvals()
    return {**build_state_response(), "ok": True, "pending": pending}
