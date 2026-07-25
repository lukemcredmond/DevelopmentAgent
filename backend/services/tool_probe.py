"""Safe smoke probes for Tool Health UI — no LLM; canned args + skip destructive tools."""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Tuple

DESTRUCTIVE_TOOLS = frozenset(
    {
        "write_file",
        "apply_patch",
        "delete_file",
        "git_commit",
        "update_board",
        "add_backlog_tasks",
        "add_subtasks",
    }
)

# run_command is slow/side-effecty — only allow a tiny echo probe when forced
RUN_COMMAND_SAFE_PROBE = {"command": "echo allhands_probe_ok"}

TOOL_HINTS: Dict[str, List[str]] = {
    "list_dir": [
        "Pass path relative to the workspace root (use '.' for root).",
    ],
    "read_file": [
        "Pass path relative to the workspace (e.g. README.md or lib/main.dart).",
        "Do not invent absolute paths outside the project.",
    ],
    "write_file": [
        "Prefer apply_patch for edits to existing files; write_file for new files.",
        "Destructive — not smoke-tested automatically.",
    ],
    "apply_patch": [
        "Call read_file first; old_text must match the file exactly.",
        "Destructive — not smoke-tested automatically.",
    ],
    "delete_file": [
        "Destructive — confirm path before calling. Not smoke-tested automatically.",
    ],
    "run_command": [
        "Use a single allowlisted command (no && chains unless enabled).",
        "Prefer project lint/test commands from the stack catalog.",
        "Skipped in Health by default (slow / side effects).",
    ],
    "grep": [
        "Provide pattern and optional path; keep patterns simple.",
    ],
    "glob_file_search": [
        "Use a glob like *.md or **/*.dart relative to the workspace.",
    ],
    "git_status": [
        "No arguments required; reports workspace git status.",
    ],
    "git_diff": [
        "Optional path; empty args show unstaged/staged summary when supported.",
    ],
    "git_commit": [
        "Destructive — requires a message and staged changes. Not auto-probed.",
    ],
    "search_code": [
        "Provide a short query string about the code you need.",
    ],
    "semantic_search": [
        "Requires Qdrant + indexed codebase when enableSemanticSearch is on.",
    ],
    "web_search": [
        "Requires enableWebSearch; pass a concise query.",
    ],
}


def _workspace_dir() -> str:
    from backend import state

    return state.WORKSPACE_DIR or "."


def _find_probe_read_path() -> Optional[str]:
    ws = _workspace_dir()
    candidates = [
        "README.md",
        "readme.md",
        "README",
        "pubspec.yaml",
        "package.json",
        "pyproject.toml",
        "Cargo.toml",
        "go.mod",
    ]
    for rel in candidates:
        full = os.path.join(ws, rel)
        if os.path.isfile(full):
            return rel.replace("\\", "/")
    # any small text file at root
    try:
        for name in sorted(os.listdir(ws))[:40]:
            full = os.path.join(ws, name)
            if os.path.isfile(full) and os.path.getsize(full) < 200_000:
                if name.endswith((".md", ".txt", ".json", ".yaml", ".yml", ".toml", ".py", ".dart")):
                    return name.replace("\\", "/")
    except OSError:
        pass
    return None


def should_skip_probe(tool_name: str, *, include_destructive: bool = False) -> Optional[str]:
    """Return skip reason or None if probe should run."""
    if tool_name in DESTRUCTIVE_TOOLS and not include_destructive:
        return "destructive"
    if tool_name == "run_command" and not include_destructive:
        return "destructive_or_slow"
    if tool_name.startswith("mcp_") and not include_destructive:
        # MCP may have side effects; skip unless forced
        return "mcp_side_effects"
    return None


def build_probe_arguments(
    tool_name: str,
    parameters: Optional[Dict[str, Any]] = None,
    *,
    include_destructive: bool = False,
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """
    Returns (args, skip_reason).
    If skip_reason is set, args may be None.
    """
    skip = should_skip_probe(tool_name, include_destructive=include_destructive)
    if skip:
        return None, skip

    if tool_name == "list_dir":
        return {"path": "."}, None
    if tool_name == "read_file":
        path = _find_probe_read_path()
        if not path:
            return None, "no_readable_file"
        return {"path": path}, None
    if tool_name == "grep":
        return {"pattern": "README", "path": "."}, None
    if tool_name == "glob_file_search":
        return {"glob_pattern": "*.md"}, None
    if tool_name == "git_status":
        return {}, None
    if tool_name == "git_diff":
        return {}, None
    if tool_name == "run_command":
        return dict(RUN_COMMAND_SAFE_PROBE), None
    if tool_name == "search_code":
        return {"query": "main"}, None
    if tool_name == "semantic_search":
        return {"query": "project overview"}, None
    if tool_name == "web_search":
        return {"query": "http"}, None
    if tool_name == "graph_query":
        return {"query": "files"}, None

    # Generic: fill required string props with placeholders
    params = parameters or {}
    props = (params.get("properties") or {}) if isinstance(params, dict) else {}
    required = params.get("required") if isinstance(params, dict) else None
    if not isinstance(required, list):
        required = []
    args: Dict[str, Any] = {}
    for key in required:
        spec = props.get(key) if isinstance(props, dict) else {}
        typ = (spec or {}).get("type") if isinstance(spec, dict) else "string"
        if typ == "integer" or typ == "number":
            args[key] = 1
        elif typ == "boolean":
            args[key] = True
        elif typ == "array":
            args[key] = []
        else:
            args[key] = "probe"
    if not args and not required:
        return {}, None
    # Fill common custom-tool fields when schema lists them
    if isinstance(props, dict):
        if "db_name" in props and "db_name" not in args:
            args["db_name"] = "local"
        if "query" in props and "query" not in args:
            args["query"] = "SELECT 1"
        if "command" in props and "command" not in args:
            args["command"] = "echo probe"
        if "url" in props and "url" not in args:
            args["url"] = "http://127.0.0.1/"
        if "input" in props and "input" not in args:
            args["input"] = "probe"
    return args, None


def build_model_hints(
    tool_name: str,
    parameters: Optional[Dict[str, Any]] = None,
    *,
    status: str = "untested",
    output: str = "",
) -> List[str]:
    hints: List[str] = []
    hints.extend(TOOL_HINTS.get(tool_name, []))
    params = parameters or {}
    props = (params.get("properties") or {}) if isinstance(params, dict) else {}
    required = params.get("required") if isinstance(params, dict) else None
    if isinstance(required, list) and required:
        hints.append(f"Required arguments: {', '.join(str(r) for r in required)}.")
    elif isinstance(props, dict) and props:
        hints.append(f"Known arguments: {', '.join(sorted(props.keys())[:12])}.")
    if status == "fail" and output:
        lower = output.lower()
        if "not found" in lower or "no such file" in lower:
            hints.append("Failure looks path-related — verify the file exists under the workspace.")
        if "not registered" in lower or "unknown tool" in lower:
            hints.append("Tool is not on this agent's allowlist — enable it under Workflow → Agent tools.")
        if "approval" in lower:
            hints.append("Tool may require approval — check Workflow tool approval settings.")
        if "timeout" in lower:
            hints.append("Command timed out — use a shorter command or raise terminal timeout.")
        if "qdrant" in lower or "semantic" in lower:
            hints.append("Semantic search needs Qdrant running and a successful Reindex.")
    if status == "skip":
        hints.append("Skipped in Health smoke tests; use Manual Test with intentional args if needed.")
    # de-dupe preserve order
    seen = set()
    out: List[str] = []
    for h in hints:
        if h not in seen:
            seen.add(h)
            out.append(h)
    return out


def run_tool_probe(
    agent_id: str,
    tool_name: str,
    *,
    arguments: Optional[Dict[str, Any]] = None,
    include_destructive: bool = False,
    parameters: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Run one smoke probe; returns status pass|fail|skip."""
    from backend.services.tool_execution_service import execute_tool, list_agent_tools

    # Resolve parameters from registry if not provided
    if parameters is None:
        for defn in list_agent_tools(agent_id):
            if defn.get("name") == tool_name:
                parameters = defn.get("parameters") or {}
                break
        parameters = parameters or {}

    skip_reason: Optional[str] = None
    probe_args: Optional[Dict[str, Any]]
    if arguments is not None:
        probe_args = dict(arguments)
        # Still block destructive unless include_destructive
        skip_reason = should_skip_probe(tool_name, include_destructive=include_destructive)
        if skip_reason:
            probe_args = None
    else:
        probe_args, skip_reason = build_probe_arguments(
            tool_name,
            parameters,
            include_destructive=include_destructive,
        )

    if skip_reason:
        hints = build_model_hints(tool_name, parameters, status="skip")
        return {
            "toolName": tool_name,
            "status": "skip",
            "success": False,
            "output": f"Skipped: {skip_reason}",
            "durationMs": 0,
            "hints": hints,
            "probeArgs": probe_args or {},
            "skipReason": skip_reason,
        }

    result = execute_tool(
        agent_id,
        tool_name,
        probe_args or {},
        task_id=None,
        source="manual",
        skip_approval=True,
        user_prompt=f"Tool Health probe: {tool_name}",
    )
    success = bool(result.success)
    status = "pass" if success else "fail"
    output = (result.tool_output or "")[:4000]
    hints = build_model_hints(tool_name, parameters, status=status, output=output)
    return {
        "toolName": tool_name,
        "status": status,
        "success": success,
        "output": output,
        "durationMs": int(result.duration_ms or 0),
        "hints": hints,
        "probeArgs": probe_args or {},
        "skipReason": None,
    }


def run_probe_all(
    agent_id: str,
    *,
    include_destructive: bool = False,
) -> List[Dict[str, Any]]:
    from backend.services.tool_execution_service import list_agent_tools

    results: List[Dict[str, Any]] = []
    for defn in list_agent_tools(agent_id):
        name = str(defn.get("name") or "")
        if not name:
            continue
        results.append(
            run_tool_probe(
                agent_id,
                name,
                include_destructive=include_destructive,
                parameters=defn.get("parameters") or {},
            )
        )
    return results
