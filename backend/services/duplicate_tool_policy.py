"""Workflow-driven duplicate tool-call policy (in-step skip/stop and cross-step blocks)."""

from __future__ import annotations

from typing import List, Tuple

from backend.services.workflow_settings import get_workflow_settings

DEFAULT_HARD_STOP_EXCLUDE = ("run_command",)

# Never cross-step block read-only exploration tools after a stuck step.
READONLY_CROSS_STEP_BLOCK_EXEMPT = frozenset(
    {"read_file", "list_dir", "grep", "glob_file_search"}
)


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
    if tool_name in exclude:
        return False
    return True


def duplicate_in_step_soft_skip_applies(tool_name: str, ws: dict | None = None) -> bool:
    """When False, identical successful calls fall through to execute_tool (cache may apply there)."""
    policy, exclude = get_duplicate_settings(ws)
    if policy == "off":
        return False
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
