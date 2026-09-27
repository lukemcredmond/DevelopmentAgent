"""Validate and persist agent-proposed custom tools."""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple

from backend import state
from backend.services.events import publish_event


def _builtin_tool_names() -> set[str]:
    from backend.agents.registry import BUILTIN_TOOL_CATALOG

    return set(BUILTIN_TOOL_CATALOG.keys())


def validate_custom_tool_def(raw: Dict[str, Any]) -> Tuple[bool, List[str], Optional[Dict[str, Any]]]:
    """Return (ok, errors, normalized_def)."""
    from backend.services.custom_tools import (
        merged_custom_tool_defs,
        normalize_custom_tool_def,
        resolve_workspace_relative_path,
        shell_command_is_safe,
    )
    from backend.services.workflow_settings import get_workflow_settings

    errors: List[str] = []
    if not isinstance(raw, dict):
        return False, ["tool definition must be a JSON object"], None

    norm = normalize_custom_tool_def(raw)
    if not norm:
        return False, ["invalid name (must be identifier like flutter_analyze)"], None

    name = norm["name"]
    if name in _builtin_tool_names():
        errors.append(f"name '{name}' conflicts with a built-in tool")
    if name == "register_custom_tool":
        errors.append("name 'register_custom_tool' is reserved")

    executor = norm.get("executor") or "shell"
    if executor not in ("shell", "http", "sql", "script"):
        errors.append(f"unsupported executor '{executor}'")

    if executor == "shell":
        cmd = str((norm.get("shell") or {}).get("command") or "").strip()
        if not cmd:
            errors.append("shell executor requires shell.command")
        elif not shell_command_is_safe(cmd):
            errors.append("shell.command matches blocked destructive patterns")

    if executor == "script":
        rel = str((norm.get("script") or {}).get("path") or "").strip()
        if not rel:
            errors.append("script executor requires script.path")
        elif not resolve_workspace_relative_path(rel):
            errors.append(f"script.path '{rel}' is invalid or escapes workspace")

    if executor == "http":
        url = str((norm.get("http") or {}).get("url") or "").strip()
        if not url:
            errors.append("http executor requires http.url")

    post = norm.get("shellPostProcess") or {}
    if isinstance(post, dict) and post:
        script_path = post.get("path") or (post.get("script") or {}).get("path")
        if script_path and not resolve_workspace_relative_path(str(script_path)):
            errors.append(f"shellPostProcess script path '{script_path}' is invalid")

    ws = get_workflow_settings()
    max_tools = int(ws.get("maxProjectCustomTools") or 50)
    existing = list_project_custom_tool_defs_only(ws)
    replacing = any(d.get("name") == name for d in existing)
    if not replacing and len(existing) >= max_tools:
        errors.append(f"project custom tool limit ({max_tools}) reached")

    for other in merged_custom_tool_defs(ws):
        if other.get("name") == name and other.get("scope") == "global" and not replacing:
            pass  # project override allowed

    if errors:
        return False, errors, norm
    return True, [], norm


def list_project_custom_tool_defs_only(settings: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    from backend.services.custom_tools import list_project_custom_tool_defs

    return list_project_custom_tool_defs(settings)


def merge_project_custom_tool(
    normalized: Dict[str, Any],
    *,
    settings: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """Append or replace-by-name in project customTools list (mutates settings dict)."""
    from backend.services.custom_tools import normalize_custom_tool_def
    from backend.services.workflow_settings import get_workflow_settings

    ws = settings if settings is not None else get_workflow_settings()
    project_defs: List[Dict[str, Any]] = []
    for raw in ws.get("customTools") or []:
        if isinstance(raw, dict):
            n = normalize_custom_tool_def(raw)
            if n and n["name"] != normalized["name"]:
                project_defs.append({k: v for k, v in n.items() if k != "scope"})
    persist = {k: v for k, v in normalized.items() if k != "scope"}
    project_defs.append(persist)
    ws["customTools"] = project_defs
    return project_defs


def _append_agent_tools_allowlist(
    ws: Dict[str, Any],
    tool_name: str,
    agents: List[str],
) -> None:
    agent_tools = ws.get("agentTools")
    if not isinstance(agent_tools, dict):
        return
    for role in agents:
        override = agent_tools.get(role)
        if not isinstance(override, list) or not override:
            continue
        names = {str(n) for n in override}
        if tool_name not in names:
            override.append(tool_name)


def register_custom_tool(
    raw: Dict[str, Any],
    *,
    source: str = "agent",
    agent_role: Optional[str] = None,
) -> Dict[str, Any]:
    """Validate, merge into project customTools, reconfigure agents. Caller should hold STATE_LOCK."""
    from backend.agents.registry import configure_agent_tools, configure_agent_prompts
    from backend.services.custom_tools import list_project_custom_tool_defs, normalize_custom_tool_def
    from backend.services.workflow_settings import get_workflow_settings, save_workflow_settings

    ok, errors, norm = validate_custom_tool_def(raw)
    if not ok or not norm:
        return {
            "ok": False,
            "errors": errors,
            "message": "; ".join(errors) if errors else "validation failed",
        }

    if agent_role and agent_role not in (norm.get("agents") or []):
        norm.setdefault("agents", [])
        if agent_role not in norm["agents"]:
            norm["agents"].append(agent_role)

    ws = get_workflow_settings()
    merge_project_custom_tool(norm, settings=ws)
    _append_agent_tools_allowlist(ws, norm["name"], norm.get("agents") or [])
    save_workflow_settings(ws)
    configure_agent_tools(ws)
    configure_agent_prompts(ws)

    publish_event(
        "activity",
        {
            "taskId": state.ACTIVE_SPRINT_TASK_ID or "system",
            "taskTitle": state.ACTIVE_SPRINT_TASK_ID or "Custom tool",
            "kind": "custom_tool_registered",
            "role": "system",
            "agent": agent_role or "System",
            "content": f"Registered custom tool '{norm['name']}' ({norm.get('executor')}, source={source})",
            "lane": None,
            "timestamp": __import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        },
    )

    return {
        "ok": True,
        "name": norm["name"],
        "executor": norm.get("executor"),
        "message": f"Custom tool '{norm['name']}' saved to project and registered on agents.",
        "tools": list_project_custom_tool_defs(ws),
    }


def register_custom_tool_from_agent(**kwargs: Any) -> str:
    """Agent-facing wrapper returning JSON string."""
    raw: Dict[str, Any]
    if isinstance(kwargs.get("tool_def"), dict):
        raw = dict(kwargs["tool_def"])
    elif kwargs.get("name"):
        raw = dict(kwargs)
    else:
        return json.dumps(
            {"ok": False, "message": "Pass tool fields (name, executor, ...) or tool_def object."},
            indent=2,
        )

    role = state.ACTIVE_SPRINT_AGENT or None
    result = register_custom_tool(raw, source="agent", agent_role=role)
    return json.dumps(result, indent=2, default=str)


def heuristic_suggest_custom_tool(alias: str, arguments: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Map common invented tool names to a project custom tool definition."""
    key = (alias or "").strip()
    if not key or not re.match(r"^[a-zA-Z_][a-zA-Z0-9_]*$", key):
        return None
    lower = key.lower().replace("-", "_")

    if ("flutter" in lower and "analyze" in lower) or lower in (
        "flutter_analyze",
        "dart_analyze",
        "flutter_lint",
    ):
        path_arg = "path"
        props: Dict[str, Any] = {
            "path": {"type": "string", "description": "Optional subdirectory or '.' for root"},
        }
        cmd = "flutter analyze {path}" if arguments.get("path") is not None else "flutter analyze"
        if "path" not in arguments and len(arguments) == 1:
            only_key = next(iter(arguments.keys()), None)
            if only_key:
                path_arg = str(only_key)
                props = {path_arg: {"type": "string"}}
                cmd = f"flutter analyze {{{path_arg}}}"
        return {
            "name": key if key not in _builtin_tool_names() else "flutter_analyze",
            "description": "Run flutter analyze and return structured analyzer findings for the LLM.",
            "parameters": {"type": "object", "properties": props, "required": []},
            "agents": ["Developer", "QA Tester"],
            "executor": "shell",
            "shell": {"command": cmd if "{path}" in cmd or "{" in cmd else "flutter analyze"},
            "shellPostProcess": {"builtin": "dart_analyze"},
        }

    return None


def suggest_custom_tool_from_unknown(
    alias: str,
    arguments: Dict[str, Any],
    *,
    brief_context: str = "",
) -> Optional[Dict[str, Any]]:
    """Suggest a custom tool def for an unknown invented name (heuristic; brief reserved for LLM)."""
    _ = brief_context
    return heuristic_suggest_custom_tool(alias, arguments)


def try_auto_forge_unknown_tool(
    alias: str,
    arguments: Dict[str, Any],
    *,
    agent_role: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """If auto-forge is enabled and we can suggest a def, register and return result."""
    from backend.services.workflow_settings import get_workflow_settings

    if not get_workflow_settings().get("autoForgeUnknownTools"):
        return None
    suggestion = suggest_custom_tool_from_unknown(alias, arguments)
    if not suggestion:
        return None
    if suggestion.get("name") in _builtin_tool_names():
        return None
    result = register_custom_tool(suggestion, source="auto_forge", agent_role=agent_role)
    if result.get("ok"):
        return result
    return None
