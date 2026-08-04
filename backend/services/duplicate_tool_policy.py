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


def normalize_run_command_for_duplicate(command: str) -> str:
    """Stable key for in-step duplicate detection on run_command."""
    text = str(command or "").strip()
    text = re.sub(r"\s+", " ", text)
    return text.lower()


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
