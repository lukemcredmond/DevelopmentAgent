"""Recover Developer steps stuck on duplicate read_file loops or blocked premature board moves."""

from __future__ import annotations

from typing import Any, Dict, Optional

_PREMATURE_LANE_VALUES = frozenset({"done", "qa", "code review"})


def is_premature_lane_update(arguments: Optional[Dict[str, Any]]) -> bool:
    args = arguments if isinstance(arguments, dict) else {}
    for key in ("stage", "lane", "status"):
        val = str(args.get(key) or "").strip().lower()
        if val in _PREMATURE_LANE_VALUES:
            return True
    return False


def read_duplicate_loop_deadlock(same_success: int, *, threshold: int = 2) -> bool:
    """True when identical read_file successes this step meet the deadlock threshold."""
    return int(same_success or 0) >= threshold


def dev_tool_loop_deadlock_active(
    *,
    duplicate_read_same_success: int = 0,
    premature_board_blocked: bool = False,
) -> bool:
    if premature_board_blocked:
        return True
    return read_duplicate_loop_deadlock(duplicate_read_same_success)


def persist_edit_target_on_task(task: Dict[str, Any]) -> str:
    from backend.services.file_blocker import resolve_dev_edit_target_path

    return str(resolve_dev_edit_target_path(task) or "").strip()


def arm_task_for_tool_loop_recovery(task: Dict[str, Any]) -> str:
    from backend.services.sprint_service import apply_read_only_no_edits_outcome

    apply_read_only_no_edits_outcome(task)
    return persist_edit_target_on_task(task)


def build_blocked_board_recovery_hint(task: Optional[Dict[str, Any]]) -> str:
    target = persist_edit_target_on_task(task) if isinstance(task, dict) else ""
    if target:
        return (
            f"No writes this step; moving to Done/QA is not allowed. "
            f"Next call MUST be apply_patch or write_file on `{target}`."
        )
    return (
        "No writes this step; moving to Done/QA is not allowed. "
        "Next call MUST be apply_patch or write_file for this card's acceptance criteria."
    )


def recover_dev_tool_loop_deadlock(task_id: str) -> None:
    """Post-step recovery: re-arm Forced Patch and optionally switch to backup model."""
    from backend.agents.task_context import find_task_by_id
    from backend.services.logs import add_system_log

    live = find_task_by_id(str(task_id or ""))
    if not live:
        return
    target = arm_task_for_tool_loop_recovery(live)
    try:
        from backend.services.backup_model import arm_backup_for_agent, should_arm_from_exit_reason

        if should_arm_from_exit_reason("duplicate_tool"):
            arm_backup_for_agent("dev", live, reason="duplicate_tool", force=True)
    except Exception:
        pass
    title = str(live.get("title") or task_id)
    hint = f" on `{target}`" if target else ""
    add_system_log(
        "Developer",
        "warning",
        f"'{title}': tool-loop deadlock recovery — Forced Patch queued{hint}.",
    )
