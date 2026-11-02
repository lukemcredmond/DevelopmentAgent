"""Recover Developer steps stuck on duplicate read_file loops or blocked premature board moves."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

_PREMATURE_LANE_VALUES = frozenset({"done", "qa", "code review"})


def is_premature_lane_update(arguments: Optional[Dict[str, Any]]) -> bool:
    args = arguments if isinstance(arguments, dict) else {}
    for key in ("stage", "lane", "status"):
        val = str(args.get(key) or "").strip().lower()
        if val in _PREMATURE_LANE_VALUES:
            return True
    target = str(args.get("target_lane") or "").strip().lower()
    return target in _PREMATURE_LANE_VALUES


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


def max_premature_board_retries(ws: Optional[Dict[str, Any]] = None) -> int:
    if ws is None:
        from backend.services.workflow_settings import get_workflow_settings

        ws = get_workflow_settings()
    try:
        return max(1, int(ws.get("maxPrematureBoardRetries") or 2))
    except (TypeError, ValueError):
        return 2


def developer_oracle_blocks_lane_advance(task_id: Optional[str]) -> bool:
    from backend.agents.task_context import find_task_by_id
    from backend.services.card_ledger import last_oracle_passed

    if not task_id:
        return False
    task = find_task_by_id(str(task_id))
    if not task:
        return False
    passed = last_oracle_passed(str(task_id), task)
    return passed is False


def filter_developer_step_tools(tools: List[Any], task_id: Optional[str]) -> List[Any]:
    """Drop update_board when oracle/lint still failing — forces fix before lane advance."""
    if not tools:
        return tools
    if not developer_oracle_blocks_lane_advance(task_id):
        return list(tools)
    filtered = [
        t
        for t in tools
        if isinstance(t, dict)
        and str((t.get("function") or {}).get("name") or "") != "update_board"
    ]
    return filtered if filtered else list(tools)


def is_developer_board_blocked_output(tool_output: str) -> bool:
    text = (tool_output or "").lower()
    if "blocked fingerprint" in text:
        return True
    if not text.strip():
        return False
    if "error:" not in text and "cannot move" not in text:
        return False
    markers = (
        "oracle fail",
        "blocks lane advance",
        "cannot move to done",
        "cannot move to qa",
        "cannot move to code review",
        "no writes this step",
        "premature_lane",
        "step exit",
    )
    return any(m in text for m in markers)


def handle_developer_board_blocked_retry(
    agent: Any,
    task_id: Optional[str],
    tool_output: str,
    *,
    ws: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    """Increment blocked-board counter; return stop message when cap reached."""
    if not task_id or not is_developer_board_blocked_output(tool_output):
        return None
    count = int(getattr(agent, "_board_blocked_count", 0) or 0) + 1
    agent._board_blocked_count = count
    agent._hide_update_board_tools = True
    from backend.agents.task_context import find_task_by_id

    live = find_task_by_id(str(task_id))
    if live:
        arm_task_for_tool_loop_recovery(live)
    cap = max_premature_board_retries(ws)
    if count < cap:
        return None
    hint = build_blocked_board_recovery_hint(live)
    return (
        f"Stopped: update_board blocked {count} time(s) this step (cap {cap}). "
        f"{hint}"
    )


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
