"""Per-step tool result cache keyed by workspace fingerprint."""

from __future__ import annotations

import hashlib
import json
import os
from typing import Any, Dict, Optional, Tuple

from backend import state

CACHEABLE_READ_TOOLS = frozenset(
    {
        "read_file",
        "grep",
        "glob_file_search",
        "list_dir",
        "git_diff",
        "git_status",
        "search_code",
        "semantic_search",
    }
)

LINT_COMMAND_MARKERS = (
    "flutter analyze",
    "dart analyze",
    "npm run lint",
    "eslint",
    "ruff check",
    " pylint",
    "mypy",
)

_STEP_CACHE: Dict[str, Dict[str, Any]] = {}
_TOUCHED_PATHS: set[str] = set()
_FINGERPRINT: Optional[str] = None


def clear_tool_cache() -> None:
    """Clear step-scoped cache (call at sprint step start)."""
    global _FINGERPRINT
    _STEP_CACHE.clear()
    _TOUCHED_PATHS.clear()
    _FINGERPRINT = None


def register_touched_path(path: str) -> None:
    if path and path.strip():
        _TOUCHED_PATHS.add(path.strip().replace("\\", "/"))


def invalidate_fingerprint() -> None:
    global _FINGERPRINT
    _FINGERPRINT = None


def _paths_for_fingerprint() -> list[str]:
    paths = set(_TOUCHED_PATHS)
    task_id = state.ACTIVE_SPRINT_TASK_ID
    if task_id:
        from backend.agents.task_context import find_task_by_id

        task = find_task_by_id(task_id)
        if task:
            for item in task.get("files") or []:
                if isinstance(item, str):
                    paths.add(item.replace("\\", "/"))
                elif isinstance(item, dict) and item.get("path"):
                    paths.add(str(item["path"]).replace("\\", "/"))
    if not paths:
        paths.update(list(state.VIRTUAL_FILESYSTEM.keys())[:80])
    return sorted(paths)


def workspace_fingerprint() -> str:
    global _FINGERPRINT
    if _FINGERPRINT is not None:
        return _FINGERPRINT

    parts: list[str] = []
    ws = state.WORKSPACE_DIR or "."
    for rel in _paths_for_fingerprint():
        full = os.path.join(ws, rel)
        if os.path.isfile(full):
            try:
                stat = os.stat(full)
                parts.append(f"{rel}:{stat.st_mtime_ns}:{stat.st_size}")
            except OSError:
                parts.append(f"{rel}:missing")
        elif rel in state.VIRTUAL_FILESYSTEM:
            content = state.VIRTUAL_FILESYSTEM[rel]
            digest = hashlib.sha256(content.encode("utf-8", errors="replace")).hexdigest()[:16]
            parts.append(f"{rel}:vfs:{digest}")

    if not parts:
        parts.append("empty")
    _FINGERPRINT = hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]
    return _FINGERPRINT


def _cache_key(tool_name: str, arguments: Dict[str, Any]) -> str:
    payload = f"{workspace_fingerprint()}|{tool_name}|{json.dumps(arguments, sort_keys=True, default=str)}"
    return hashlib.sha256(payload.encode()).hexdigest()


def _is_lint_command(command: str) -> bool:
    lower = (command or "").lower()
    return any(marker in lower for marker in LINT_COMMAND_MARKERS)


def should_cache_tool(tool_name: str, source: str) -> bool:
    if source != "agent":
        return False
    if tool_name in CACHEABLE_READ_TOOLS:
        return True
    return tool_name == "run_command"


def get_cached_result(
    tool_name: str,
    arguments: Dict[str, Any],
) -> Optional[Tuple[str, bool]]:
    key = _cache_key(tool_name, arguments)
    entry = _STEP_CACHE.get(key)
    if not entry:
        return None
    output = str(entry.get("output") or "")
    success = entry.get("success") is not False
    if "[cached" not in output:
        output = f"{output}\n[cached — workspace unchanged since last call]"
    return output, success


def store_cached_result(
    tool_name: str,
    arguments: Dict[str, Any],
    output: str,
    success: bool,
) -> None:
    _STEP_CACHE[_cache_key(tool_name, arguments)] = {
        "output": output,
        "success": success,
    }


def check_run_command_cache(command: str, arguments: Dict[str, Any]) -> Optional[str]:
    """Soft block repeated lint commands when workspace fingerprint is unchanged."""
    if not _is_lint_command(command):
        return None
    key = _cache_key("run_command", arguments)
    entry = _STEP_CACHE.get(key)
    if not entry:
        return None

    prev_output = str(entry.get("output") or "")
    from backend.services.diagnostics_parser import parse_command_diagnostics

    diagnostics = parse_command_diagnostics(command, prev_output)
    if diagnostics:
        return (
            f"[findings exit 1]\n{command}\n"
            f"Summary: {len(diagnostics)} problem(s) still open — workspace unchanged since last run.\n"
            "Fix each file:line from the previous result before re-running.\n\n"
            f"## Previous result\n{prev_output[:4000]}"
        )
    return f"{prev_output}\n[cached — workspace unchanged; prior clean result returned]"
