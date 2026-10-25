"""Repair board tool arguments when models omit required fields."""

from __future__ import annotations

from typing import Any, Dict, Optional


def repair_sprint_board_tool_arguments(
    tool_name: str,
    arguments: Any,
    *,
    task_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Fill task_id / target_lane for recovered PO update_board calls."""
    if not isinstance(arguments, dict):
        return {}
    args = dict(arguments)
    if tool_name != "update_board":
        return args
    from backend import state

    active = (task_id or state.ACTIVE_SPRINT_TASK_ID or "").strip()
    if active and not str(args.get("task_id") or "").strip():
        args["task_id"] = active
    if not str(args.get("target_lane") or "").strip():
        args["target_lane"] = "In Progress"
    return args
