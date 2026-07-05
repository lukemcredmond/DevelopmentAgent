"""Persistent per-project tool aliases and pending unknown-tool requests."""

import json
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from backend import state
from backend.services.events import publish_event


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def get_aliases(project_id: Optional[str] = None) -> Dict[str, Dict[str, Any]]:
    pid = project_id or state.CURRENT_PROJECT_ID
    return state.storage.get_tool_aliases(pid)


def save_alias(
    alias: str,
    target_tool: str,
    default_args: Optional[Dict[str, Any]] = None,
    project_id: Optional[str] = None,
) -> None:
    pid = project_id or state.CURRENT_PROJECT_ID
    state.storage.save_tool_alias(pid, alias, target_tool, default_args or {})


def delete_alias(alias: str, project_id: Optional[str] = None) -> None:
    pid = project_id or state.CURRENT_PROJECT_ID
    state.storage.delete_tool_alias(pid, alias)


BUILTIN_TOOL_ALIASES: Dict[str, str] = {
    "Grep": "grep",
    "grep_search": "grep",
    "Glob": "glob_file_search",
    "glob": "glob_file_search",
    "glob_search": "glob_file_search",
}


def resolve_tool_call(
    alias: str,
    arguments: Dict[str, Any],
    project_id: Optional[str] = None,
) -> Tuple[str, Dict[str, Any], bool]:
    """Returns (tool_name, merged_args, was_resolved)."""
    builtin = BUILTIN_TOOL_ALIASES.get(alias)
    if builtin:
        return builtin, arguments, True

    aliases = get_aliases(project_id)
    mapping = aliases.get(alias)
    if not mapping:
        return alias, arguments, False

    target = mapping.get("tool") or mapping.get("targetTool") or ""
    default_args = dict(mapping.get("args") or mapping.get("defaultArgs") or {})
    merged = {**default_args, **arguments}

    if target == "run_command" and "command" not in merged:
        if len(arguments) == 1:
            only_val = next(iter(arguments.values()), "")
            if isinstance(only_val, str):
                merged["command"] = only_val
        elif alias.replace("-", "_").lower().find("flutter") >= 0:
            merged["command"] = "flutter analyze"

    return target, merged, True


def queue_pending_tool(
    alias: str,
    arguments: Dict[str, Any],
    *,
    task_id: Optional[str] = None,
    agent_role: Optional[str] = None,
) -> Dict[str, Any]:
    request = {
        "id": str(uuid.uuid4())[:12],
        "projectId": state.CURRENT_PROJECT_ID,
        "taskId": task_id,
        "agentRole": agent_role,
        "alias": alias,
        "arguments": arguments,
        "status": "pending",
        "timestamp": _now(),
    }
    state.PENDING_TOOL_REQUESTS.append(request)
    state.storage.save_pending_tool_request(request)

    publish_event(
        "activity",
        {
            "taskId": task_id or "system",
            "taskTitle": task_id or "Unknown tool",
            "kind": "pending_tool",
            "role": "system",
            "agent": agent_role or "System",
            "content": f"Unknown tool '{alias}' — map it to a registered action in the UI.",
            "lane": None,
            "timestamp": request["timestamp"],
        },
    )
    publish_event("pending_tool", request)
    return request


def list_pending_tools(project_id: Optional[str] = None) -> List[Dict[str, Any]]:
    pid = project_id or state.CURRENT_PROJECT_ID
    memory = [r for r in state.PENDING_TOOL_REQUESTS if r.get("projectId") == pid and r.get("status") == "pending"]
    stored = state.storage.list_pending_tool_requests(pid, status="pending")
    seen = {r["id"] for r in memory}
    for row in stored:
        if row["id"] not in seen:
            memory.append(row)
    return memory


def resolve_pending_tool(
    request_id: str,
    target_tool: str,
    default_args: Optional[Dict[str, Any]] = None,
    *,
    save_mapping: bool = True,
) -> Optional[Dict[str, Any]]:
    pending = None
    for req in state.PENDING_TOOL_REQUESTS:
        if req.get("id") == request_id:
            pending = req
            break
    if not pending:
        pending = state.storage.get_pending_tool_request(request_id)
    if not pending:
        return None

    alias = pending["alias"]
    args = dict(default_args or {})
    if target_tool == "run_command" and "command" not in args:
        if "flutter" in alias.lower():
            args["command"] = "flutter analyze"
        elif pending.get("arguments"):
            first = next(iter(pending["arguments"].values()), None)
            if isinstance(first, str):
                args["command"] = first

    if save_mapping:
        save_alias(alias, target_tool, args)

    pending["status"] = "resolved"
    state.storage.update_pending_tool_status(request_id, "resolved")

    publish_event(
        "activity",
        {
            "taskId": pending.get("taskId") or "system",
            "taskTitle": alias,
            "kind": "tool_alias_saved",
            "role": "user",
            "agent": "User",
            "content": f"Mapped '{alias}' → {target_tool}",
            "timestamp": _now(),
        },
    )
    return {"alias": alias, "targetTool": target_tool, "defaultArgs": args}


def load_pending_tools_for_project(project_id: str) -> None:
    state.PENDING_TOOL_REQUESTS = state.storage.list_pending_tool_requests(
        project_id, status="pending"
    )
