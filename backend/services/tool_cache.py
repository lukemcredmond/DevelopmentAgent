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

# Idempotent probes — safe to reuse within a step when fingerprint is unchanged.

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


def tool_arguments_equal(a: Dict[str, Any], b: Dict[str, Any]) -> bool:
    return json.dumps(a or {}, sort_keys=True, default=str) == json.dumps(
        b or {}, sort_keys=True, default=str
    )


def tool_arguments_eligible_for_cache(tool_name: str, arguments: Dict[str, Any]) -> bool:
    """Invalid args should never hit step cache — always execute fresh."""
    args = arguments if isinstance(arguments, dict) else {}
    if tool_name == "grep":
        return bool(str(args.get("pattern") or "").strip())
    if tool_name == "glob_file_search":
        return bool(str(args.get("pattern") or "").strip())
    if tool_name == "search_code":
        return bool(str(args.get("query") or "").strip())
    if tool_name == "read_file":
        return bool(str(args.get("path") or "").strip())
    if tool_name == "list_dir":
        return True
    if tool_name == "run_command":
        return bool(str(args.get("command") or "").strip())
    return True


def is_substantive_tool_output(
    tool_name: str,
    arguments: Dict[str, Any],
    output: str,
) -> bool:
    """False → do not cache or replay; force a real tool execution."""
    text = str(output or "").strip()
    if not text:
        return False
    if not tool_arguments_eligible_for_cache(tool_name, arguments):
        return False
    lower = text.lower()
    if lower.startswith("error:"):
        return False
    if "no matches for pattern ''" in lower or "no matches for pattern \"\"" in lower:
        return False
    if text.startswith("[skipped duplicate]") and "replayed" not in lower:
        return False
    if text.startswith("[blocked fingerprint]"):
        return False
    # Cache-only stub with no prior body (legacy bad entries)
    if lower.startswith("no matches for pattern") and "[cached" in lower and len(text) < 120:
        pat = str((arguments or {}).get("pattern") or "").strip()
        if not pat:
            return False
    return True


def find_prior_tool_output_for_task(
    task: Dict[str, Any],
    tool_name: str,
    arguments: Dict[str, Any],
) -> Optional[str]:
    """Last successful tool output for matching tool+args (transcript / tool log)."""
    args = arguments if isinstance(arguments, dict) else {}
    skip_markers = ("[skipped duplicate]", "[blocked fingerprint]", "[cached")

    def _accept_output(text: str) -> bool:
        head = (text or "").strip()[:120].lower()
        if not text:
            return False
        if any(m in head for m in skip_markers):
            return False
        return is_substantive_tool_output(tool_name, args, text)

    for entry in reversed(task.get("transcript") or []):
        if not isinstance(entry, dict):
            continue
        if str(entry.get("toolName") or "") != tool_name:
            continue
        if entry.get("toolSuccess") is False:
            continue
        entry_args = entry.get("toolArgs")
        if not isinstance(entry_args, dict):
            entry_args = {}
        if not tool_arguments_equal(entry_args, args):
            continue
        for field in ("toolOutput", "content"):
            out = str(entry.get(field) or "")
            if _accept_output(out):
                return out

    task_id = str(task.get("id") or "")
    if task_id:
        try:
            from backend.services.tool_execution_service import get_tool_history

            for ev in reversed(get_tool_history() or []):
                if str(ev.get("toolName") or "") != tool_name:
                    continue
                if ev.get("success") is False:
                    continue
                if str(ev.get("taskId") or "") != task_id:
                    continue
                ev_args = ev.get("toolArgs") or ev.get("arguments") or {}
                if not isinstance(ev_args, dict):
                    ev_args = {}
                if not tool_arguments_equal(ev_args, args):
                    continue
                out = str(ev.get("toolOutput") or "")
                if _accept_output(out):
                    return out
        except Exception:
            pass
    return None


def resolve_duplicate_replay(
    tool_name: str,
    arguments: Dict[str, Any],
    task: Optional[Dict[str, Any]],
) -> Optional[Tuple[str, bool]]:
    """Last successful output for duplicate skip, or None to allow a real tool run."""
    if not tool_arguments_eligible_for_cache(tool_name, arguments):
        return None
    cached = get_cached_result(tool_name, arguments)
    if cached:
        return cached
    if task:
        prior = find_prior_tool_output_for_task(task, tool_name, arguments)
        if prior:
            from backend.services.llm_context import truncate_tool_output_for_llm

            body = truncate_tool_output_for_llm(tool_name, prior)
            return (
                "[skipped duplicate — prior result replayed; do not call again with identical args]\n"
                + body,
                True,
            )
    return None


def format_duplicate_skip_output(
    tool_name: str,
    *,
    arguments: Dict[str, Any],
    task: Optional[Dict[str, Any]],
    fallback_summary: str,
) -> str:
    """Build tool message for in-step duplicate skip (replay prior output when possible)."""
    replay = resolve_duplicate_replay(tool_name, arguments, task)
    if replay:
        return replay[0]
    if tool_name == "run_command":
        cmd = str((arguments or {}).get("command") or fallback_summary)[:120]
        return (
            f"[skipped duplicate] Command already succeeded in this step"
            + (f" ({cmd})" if cmd else "")
            + ". Do not re-run — proceed to verification (lint/analyze/build) "
            "or update_board when acceptance criteria are met."
        )
    return (
        f"[skipped duplicate] Already ran '{tool_name}' with identical args"
        + (f" ({fallback_summary[:120]})" if fallback_summary else "")
        + ". Use prior output in the conversation; change approach or edit files."
    )


def _cache_key(tool_name: str, arguments: Dict[str, Any]) -> str:
    payload = f"{workspace_fingerprint()}|{tool_name}|{json.dumps(arguments, sort_keys=True, default=str)}"
    return hashlib.sha256(payload.encode()).hexdigest()


def _is_lint_command(command: str) -> bool:
    lower = (command or "").lower()
    return any(marker in lower for marker in LINT_COMMAND_MARKERS)


def _is_probe_command(command: str) -> bool:
    """True for --version / --help style idempotent probes."""
    cmd = (command or "").strip()
    if not cmd:
        return False
    lower = cmd.lower()
    if "--version" in lower or "--help" in lower:
        return True
    # Trailing short flags: `flutter -v`, `tool -h`
    tokens = lower.replace("\\", "/").split()
    if not tokens:
        return False
    last = tokens[-1]
    return last in ("-v", "-V", "-h", "version", "help")


def is_probe_command(command: str) -> bool:
    return _is_probe_command(command)


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
    if not tool_arguments_eligible_for_cache(tool_name, arguments):
        _STEP_CACHE.pop(key, None)
        return None
    entry = _STEP_CACHE.get(key)
    if not entry:
        return None
    output = str(entry.get("output") or "")
    success = entry.get("success") is not False
    if not is_substantive_tool_output(tool_name, arguments, output):
        _STEP_CACHE.pop(key, None)
        return None
    if "[cached" not in output:
        output = f"{output}\n[cached — workspace unchanged since last call]"
    return output, success


def store_cached_result(
    tool_name: str,
    arguments: Dict[str, Any],
    output: str,
    success: bool,
) -> None:
    if not success:
        return
    if not tool_arguments_eligible_for_cache(tool_name, arguments):
        return
    if not is_substantive_tool_output(tool_name, arguments, output):
        return
    _STEP_CACHE[_cache_key(tool_name, arguments)] = {
        "output": output,
        "success": success,
    }


def check_run_command_cache(command: str, arguments: Dict[str, Any]) -> Optional[str]:
    """Soft block repeated lint/probe commands when workspace fingerprint is unchanged."""
    key = _cache_key("run_command", arguments)
    entry = _STEP_CACHE.get(key)
    if not entry:
        return None

    prev_output = str(entry.get("output") or "")

    if _is_probe_command(command):
        return f"{prev_output}\n[cached — identical probe; workspace unchanged]"

    if not _is_lint_command(command):
        return None

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
