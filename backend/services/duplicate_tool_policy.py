"""Workflow-driven duplicate tool-call policy (in-step skip/stop and cross-step blocks)."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from backend.services.workflow_settings import get_workflow_settings

DEFAULT_HARD_STOP_EXCLUDE: Tuple[str, ...] = ()

# Never cross-step block read-only exploration tools after a stuck step.
READONLY_CROSS_STEP_BLOCK_EXEMPT = frozenset(
    {"read_file", "list_dir", "grep", "glob_file_search"}
)


def in_step_duplicate_seed_exempt_tools() -> frozenset[str]:
    """Read/cache tools: do not pre-seed successful_tool_keys from task fingerprints."""
    from backend.services.tool_cache import CACHEABLE_READ_TOOLS

    return CACHEABLE_READ_TOOLS


def filter_tool_keys_for_in_step_seed(keys: List[tuple[str, str]]) -> List[tuple[str, str]]:
    exempt = in_step_duplicate_seed_exempt_tools()
    return [k for k in keys if k[0] not in exempt]


def purge_read_file_success_keys_for_path(
    successful_tool_keys: List[Tuple[str, str]],
    path: str,
) -> None:
    """Drop in-step duplicate keys for read_file on path after apply_patch/write_file."""
    import json

    from backend.workspace.files import resolve_workspace_path

    if not path.strip():
        return
    try:
        target_safe = resolve_workspace_path(path)
    except ValueError:
        target_safe = path.strip()
    keep: List[Tuple[str, str]] = []
    for name, args_json in successful_tool_keys:
        if name != "read_file":
            keep.append((name, args_json))
            continue
        try:
            args = json.loads(args_json)
        except json.JSONDecodeError:
            keep.append((name, args_json))
            continue
        p = str(args.get("path") or "")
        try:
            if resolve_workspace_path(p) != target_safe:
                keep.append((name, args_json))
        except ValueError:
            if p != path.strip():
                keep.append((name, args_json))
    successful_tool_keys[:] = keep


_VERIFY_COMMAND_RE = re.compile(
    r"(?:"
    r"flutter\s+test|dart\s+test|dotnet\s+test|"
    r"pytest|npm\s+test|pnpm\s+test|yarn\s+test|"
    r"go\s+test|cargo\s+test|mvn\s+test|gradlew?\s+test"
    r")",
    re.I,
)


def normalize_run_command_for_duplicate(command: str) -> str:
    """Stable key for in-step duplicate detection on run_command."""
    text = str(command or "").strip()
    text = re.sub(r"\s+", " ", text)
    return text.lower()


def is_verify_command(command: str) -> bool:
    """True for test/verify shell commands (flutter test, pytest, …)."""
    return bool(_VERIFY_COMMAND_RE.search(str(command or "")))


def _command_from_tool_key(name: str, args_json: str) -> str:
    import json

    if name == "run_test":
        return "run_test"
    if name != "run_command":
        return ""
    try:
        args = json.loads(args_json or "{}")
    except json.JSONDecodeError:
        args = {}
    if isinstance(args, dict):
        return str(args.get("command") or "")
    return ""


def successful_keys_include_verify(
    successful_tool_keys: Optional[List[Tuple[str, str]]],
) -> bool:
    """True when a prior successful (or seeded) verify command is already known."""
    for name, args_json in successful_tool_keys or []:
        if name == "run_test":
            return True
        if name == "run_command" and is_verify_command(_command_from_tool_key(name, args_json)):
            return True
    return False


def tools_log_has_verify(tools_log: Optional[List[Dict[str, Any]]]) -> bool:
    """True when this step already ran or duplicate-skipped a verify command."""
    for entry in tools_log or []:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("toolName") or entry.get("name") or "")
        summary = str(entry.get("summary") or "")
        output = str(entry.get("output") or entry.get("toolOutput") or "")
        blob = f"{summary} {output}"
        ok = bool(entry.get("success"))
        skipped = "skipped duplicate" in blob.lower() or bool(entry.get("duplicateSkip"))
        if name == "run_test" and (ok or skipped):
            return True
        if name == "run_command" and (ok or skipped) and is_verify_command(blob):
            return True
    return False


WRITE_DUP_VERIFY_STOP_SNIPPET = (
    "files already written this step and verify command was a duplicate skip"
)


def agent_result_is_write_dup_verify_stop(result: Optional[str]) -> bool:
    return WRITE_DUP_VERIFY_STOP_SNIPPET in str(result or "").lower()


def verify_known_for_advance(
    tools_log: Optional[List[Dict[str, Any]]],
    agent_result: Optional[str] = None,
) -> bool:
    """True when this step's tools_log has verify, or the agent stopped as write+dup-verify."""
    if tools_log_has_verify(tools_log):
        return True
    return agent_result_is_write_dup_verify_stop(agent_result)


def duplicate_run_command_policy(ws: dict | None = None) -> str:
    settings = ws if ws is not None else get_workflow_settings()
    policy = str(settings.get("duplicateRunCommandPolicy") or "strict").strip().lower()
    if policy not in ("strict", "off"):
        policy = "strict"
    return policy


def get_duplicate_settings(ws: dict | None = None) -> Tuple[str, List[str]]:
    settings = ws if ws is not None else get_workflow_settings()
    policy = str(settings.get("duplicateToolPolicy") or "strict").strip().lower()
    if policy not in ("strict", "cache_only", "off"):
        policy = "strict"
    raw_exclude = settings.get("duplicateToolHardStopExclude")
    if isinstance(raw_exclude, list) and raw_exclude:
        exclude = [str(x).strip() for x in raw_exclude if str(x).strip()]
    else:
        exclude = list(DEFAULT_HARD_STOP_EXCLUDE)
    return policy, exclude


def duplicate_in_step_hard_stop_applies(tool_name: str, ws: dict | None = None) -> bool:
    policy, exclude = get_duplicate_settings(ws)
    if policy == "off":
        return False
    if tool_name == "run_command" and duplicate_run_command_policy(ws) == "strict":
        return True
    if tool_name in exclude:
        return False
    return True


def duplicate_in_step_soft_skip_applies(tool_name: str, ws: dict | None = None) -> bool:
    """When False, identical successful calls fall through to execute_tool (cache may apply there)."""
    policy, exclude = get_duplicate_settings(ws)
    if policy == "off":
        return False
    if tool_name == "run_command" and duplicate_run_command_policy(ws) == "strict":
        return True
    if tool_name in exclude:
        return False
    return True


def _duplicate_args_summary(tool_name: str, arguments: Dict[str, Any]) -> str:
    args = arguments if isinstance(arguments, dict) else {}
    if tool_name == "run_command":
        return str(args.get("command") or "")[:100]
    if tool_name == "grep":
        return f"pattern={str(args.get('pattern') or '')[:60]}"
    if tool_name == "glob_file_search":
        return f"pattern={str(args.get('pattern') or '')[:60]}"
    if tool_name == "read_file":
        return str(args.get("path") or "")[:100]
    if tool_name == "list_dir":
        return str(args.get("path") or ".")[:100]
    if tool_name == "search_code":
        return f"query={str(args.get('query') or '')[:60]}"
    return ""


def _suggested_next_after_duplicate(tool_name: str, arguments: Dict[str, Any]) -> str:
    if tool_name == "run_command":
        return (
            "Run a different command (lint/analyze/build from acceptance criteria) "
            "or update_board when AC are met."
        )
    if tool_name == "read_file":
        path = str((arguments or {}).get("path") or "")
        return f"Use the file content above — call apply_patch on '{path}' or move on; do not read again."
    if tool_name in ("grep", "glob_file_search", "search_code", "semantic_search", "list_dir"):
        return (
            "Use the listing/matches above to edit files (apply_patch/write_file), "
            "try different search args, or run verification — do not repeat identical search."
        )
    return "Change approach: edit files, use prior tool output, or update_board / escalate."


def duplicate_loop_should_hard_stop(
    identical_prior_successes: int,
    *,
    limit: int = 3,
) -> bool:
    """True when another identical call must not replay — step should stop."""
    return identical_prior_successes >= limit


def format_duplicate_loop_breaker(
    tool_name: str,
    arguments: Dict[str, Any],
    *,
    identical_prior_successes: int,
    limit: int = 3,
) -> str:
    """Header prepended to replayed duplicate tool output to break LLM retry loops."""
    attempt = identical_prior_successes + 1
    summary = _duplicate_args_summary(tool_name, arguments)
    args_clause = f" ({summary})" if summary else ""
    next_step = _suggested_next_after_duplicate(tool_name, arguments)
    remaining = max(0, limit - identical_prior_successes)

    if attempt <= 2:
        return (
            f"[duplicate tool — call #{attempt} this step] "
            f"You already ran '{tool_name}' with the same arguments{args_clause}. "
            f"Output is unchanged — do NOT call this tool again with these args. "
            f"NEXT: {next_step}"
        )
    if identical_prior_successes < limit:
        return (
            f"[LOOP WARNING — call #{attempt}] Repeated identical '{tool_name}'{args_clause}. "
            f"The workspace has not changed; replaying the same result. "
            f"STOP retrying this tool. {next_step} "
            f"(After {remaining} more identical attempt(s) this step will hard-stop.)"
        )
    return (
        f"[LOOP STOP] '{tool_name}'{args_clause} was invoked too many times with identical args. "
        f"Do not call it again. {next_step}"
    )


def apply_duplicate_loop_breaker_to_output(
    tool_name: str,
    arguments: Dict[str, Any],
    body: str,
    *,
    identical_prior_successes: int,
    limit: int = 3,
) -> str:
    header = format_duplicate_loop_breaker(
        tool_name,
        arguments,
        identical_prior_successes=identical_prior_successes,
        limit=limit,
    )
    text = str(body or "").strip()
    if not header:
        return text
    if text.startswith("[duplicate tool") or text.startswith("[LOOP WARNING") or text.startswith("[LOOP STOP"):
        return text
    return f"{header}\n\n{text}"


def duplicate_cross_step_block_applies(
    tool_name: str,
    *,
    stop_reason: str = "",
    ws: dict | None = None,
) -> bool:
    if tool_name in READONLY_CROSS_STEP_BLOCK_EXEMPT:
        return False
    policy, exclude = get_duplicate_settings(ws)
    if policy == "off":
        return False
    reason = str(stop_reason or "").strip().lower()
    if tool_name in exclude and reason != "tool_failure_stop":
        return False
    return True
