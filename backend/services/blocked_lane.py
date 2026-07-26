"""Automatic Blocked lane for healthy dependency waits."""

from __future__ import annotations

from typing import Any, Dict, Optional, Set

from backend import state
from backend.agents.task_context import (
    normalize_task,
    record_task_decision,
    task_dependencies_met,
)
from backend.services.logs import add_system_log
from backend.services.workflow_settings import get_workflow_settings

BLOCKED_LANE = "Blocked"

# Only park cards that are waiting in planning/ready queues — never yank active work.
_ENTER_FROM: Set[str] = {"Backlog", "Refinement", "Pending Approval"}


def blocked_lane_enabled() -> bool:
    return bool(get_workflow_settings().get("enableBlockedLane", True))


def _return_lane_for(task: Dict[str, Any]) -> str:
    saved = str(task.get("blockedReturnLane") or "").strip()
    if saved and saved != BLOCKED_LANE:
        return saved
    ws = get_workflow_settings()
    if ws.get("requireBacklogRefinement") and not task.get("refinementComplete"):
        return "Refinement"
    return "Backlog"


def _move_task_to_lane(task: Dict[str, Any], source_lane: str, target_lane: str, *, reason: str) -> None:
    """Internal move assuming STATE_LOCK is held. Does not recurse into sync."""
    tid = str(task.get("id") or "")
    if not tid or source_lane == target_lane:
        return
    needle = tid
    for lane in list(state.SHARED_BOARD.keys()):
        state.SHARED_BOARD[lane] = [
            t for t in state.SHARED_BOARD[lane] if str(t.get("id", "")) != needle
        ]
    task["status"] = target_lane
    normalize_task(task)
    state.SHARED_BOARD.setdefault(target_lane, []).append(task)
    record_task_decision(
        tid,
        state.ACTIVE_SPRINT_AGENT or "System",
        "blocked_lane",
        reason,
    )


def sync_blocked_lane(
    *,
    persist: bool = True,
    completed_task_id: Optional[str] = None,
) -> Dict[str, int]:
    """
    Enter Blocked for unmet deps (from Backlog/Refinement/Pending Approval).
    Release Blocked cards whose deps are all Done.
    """
    if not blocked_lane_enabled():
        return {"entered": 0, "released": 0}

    from backend.services.board_lanes import normalize_board_lanes

    normalize_board_lanes(state.SHARED_BOARD)
    state.SHARED_BOARD.setdefault(BLOCKED_LANE, [])

    entered = 0
    released = 0
    release_targets: Dict[str, int] = {}

    with state.STATE_LOCK:
        # Release first so a just-completed dep unblocks parents before we re-park others
        for task in list(state.SHARED_BOARD.get(BLOCKED_LANE, [])):
            if not isinstance(task, dict):
                continue
            normalize_task(task)
            if not task.get("blockedBy"):
                target = _return_lane_for(task)
                task.pop("blockedReturnLane", None)
                _move_task_to_lane(
                    task,
                    BLOCKED_LANE,
                    target,
                    reason=f"Left Blocked → '{target}' (no blockedBy)",
                )
                released += 1
                release_targets[target] = release_targets.get(target, 0) + 1
                continue
            if task_dependencies_met(task):
                target = _return_lane_for(task)
                task.pop("blockedReturnLane", None)
                _move_task_to_lane(
                    task,
                    BLOCKED_LANE,
                    target,
                    reason=f"Deps Done — left Blocked → '{target}'",
                )
                released += 1
                release_targets[target] = release_targets.get(target, 0) + 1

        # Enter Blocked from waiting lanes
        for lane in list(_ENTER_FROM):
            for task in list(state.SHARED_BOARD.get(lane, [])):
                if not isinstance(task, dict):
                    continue
                normalize_task(task)
                if not (task.get("blockedBy") or []):
                    continue
                if task_dependencies_met(task):
                    continue
                task["blockedReturnLane"] = lane
                _move_task_to_lane(
                    task,
                    lane,
                    BLOCKED_LANE,
                    reason=f"Waiting on deps — '{lane}' → Blocked",
                )
                entered += 1

        if persist and (entered or released):
            from backend.services.project_service import save_current_project_state
            from backend.services.sse import publish_board_update

            save_current_project_state()
            try:
                publish_board_update(source="blocked_lane_sync")
            except Exception:
                pass

    if released:
        dest = "/".join(sorted(release_targets.keys())) or "Backlog/Refinement"
        if completed_task_id:
            add_system_log(
                "System",
                "info",
                f"Unblocked {released} card(s) → {dest} after {completed_task_id} completed",
            )
        else:
            add_system_log(
                "System",
                "info",
                f"Unblocked {released} card(s) → {dest}",
            )
    if entered:
        add_system_log(
            "System",
            "info",
            f"Blocked lane sync — entered {entered}, released {released}",
        )
    elif released and not completed_task_id:
        pass  # already logged above
    return {"entered": entered, "released": released}


def release_blocked_waiting_on(done_task_id: str) -> Dict[str, int]:
    """After a task reaches Done, release any Blocked cards that depended on it."""
    if not blocked_lane_enabled():
        return {"entered": 0, "released": 0}
    return sync_blocked_lane(persist=True, completed_task_id=str(done_task_id))
