"""Persistent per-project tool aliases and pending unknown-tool requests."""

import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Set, Tuple

from backend import state
from backend.services.events import publish_event

# Real app tools — missing from an agent means role/mode gating, not "unknown invent".
CANONICAL_TOOL_NAMES: Set[str] = {
    "write_file",
    "apply_patch",
    "delete_file",
    "read_file",
    "list_dir",
    "run_test",
    "run_command",
    "update_board",
    "add_backlog_tasks",
    "add_subtasks",
    "grep",
    "glob_file_search",
    "search_code",
    "semantic_search",
    "graph_query",
    "web_search",
    "git_status",
    "git_diff",
    "git_commit",
    "git_init",
}

BUILTIN_TOOL_ALIASES: Dict[str, str] = {
    # Grep / glob
    "Grep": "grep",
    "grep_search": "grep",
    "Glob": "glob_file_search",
    "glob": "glob_file_search",
    "glob_search": "glob_file_search",
    # Write / create
    "Write": "write_file",
    "write": "write_file",
    "WriteFile": "write_file",
    "create_file": "write_file",
    "CreateFile": "write_file",
    # Edit / patch
    "Edit": "apply_patch",
    "StrReplace": "apply_patch",
    "search_replace": "apply_patch",
    "SearchReplace": "apply_patch",
    # Shell
    "Bash": "run_command",
    "Shell": "run_command",
    "run": "run_command",
    # Read
    "Read": "read_file",
    "ReadFile": "read_file",
}

# Case-insensitive lookup for invents like "WRITE" / "create_File"
_BUILTIN_ALIASES_LOWER: Dict[str, str] = {k.lower(): v for k, v in BUILTIN_TOOL_ALIASES.items()}


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def is_canonical_tool(name: str) -> bool:
    if not name:
        return False
    if name in CANONICAL_TOOL_NAMES:
        return True
    try:
        from backend.services.custom_tools import get_custom_canonical_names

        return name in get_custom_canonical_names()
    except Exception:
        return False


def gated_tool_unavailable_message(
    tool_name: str,
    *,
    original_name: Optional[str] = None,
    agent_role: Optional[str] = None,
) -> str:
    """Clear error when a real tool is missing from this agent's registry."""
    role = (agent_role or state.ACTIVE_SPRINT_AGENT or "this agent").strip() or "this agent"
    shown = original_name or tool_name
    if state.REFINEMENT_MODE and tool_name in ("write_file", "apply_patch", "delete_file", "run_command"):
        return (
            f"Error: Tool '{shown}' is disabled during refinement. "
            "Use add_subtasks / update_board (and read/grep) instead of write tools."
        )
    return (
        f"Error: Tool '{shown}' is not available to {role}. "
        "It is a registered app tool but not enabled for this agent or mode."
    )


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


def _coalesce_run_command_args(alias: str, arguments: Dict[str, Any], merged: Dict[str, Any]) -> Dict[str, Any]:
    if "command" in merged:
        return merged
    if len(arguments) == 1:
        only_val = next(iter(arguments.values()), "")
        if isinstance(only_val, str):
            merged["command"] = only_val
    elif alias.replace("-", "_").lower().find("flutter") >= 0:
        merged["command"] = "flutter analyze"
    return merged


def resolve_tool_call(
    alias: str,
    arguments: Dict[str, Any],
    project_id: Optional[str] = None,
) -> Tuple[str, Dict[str, Any], bool]:
    """Returns (tool_name, merged_args, was_resolved)."""
    builtin = BUILTIN_TOOL_ALIASES.get(alias) or _BUILTIN_ALIASES_LOWER.get(alias.lower())
    if builtin:
        merged = dict(arguments)
        if builtin == "run_command":
            merged = _coalesce_run_command_args(alias, arguments, merged)
        return builtin, merged, True

    aliases = get_aliases(project_id)
    mapping = aliases.get(alias)
    if not mapping:
        return alias, arguments, False

    target = mapping.get("tool") or mapping.get("targetTool") or ""
    default_args = dict(mapping.get("args") or mapping.get("defaultArgs") or {})
    merged = {**default_args, **arguments}

    if target == "run_command":
        merged = _coalesce_run_command_args(alias, arguments, merged)

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
