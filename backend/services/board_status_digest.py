"""Build compact board status digests for Discord / phone notify."""

from __future__ import annotations

import hashlib
from typing import Any, Dict, List, Optional

from backend import state

_LANE_ORDER = (
    "Features",
    "Pending Approval",
    "Backlog",
    "Refinement",
    "In Progress",
    "Needs PO",
    "Needs User",
    "Code Review",
    "QA",
    "Done",
)

# Always include in rollup (even at 0)
_ALWAYS = frozenset(
    {
        "Features",
        "Backlog",
        "Refinement",
        "In Progress",
        "Needs PO",
        "Needs User",
        "QA",
        "Done",
    }
)

# Include when the lane exists on the board (even empty) or has cards
_WHEN_PRESENT = frozenset({"Pending Approval", "Code Review"})


def _lane_counts(board: Optional[Dict[str, Any]] = None) -> Dict[str, int]:
    b = board if board is not None else state.SHARED_BOARD
    counts: Dict[str, int] = {}
    for lane, tasks in (b or {}).items():
        if not isinstance(tasks, list):
            continue
        counts[str(lane)] = sum(1 for t in tasks if isinstance(t, dict))
    return counts


def build_board_status_digest(
    *,
    active_task: Optional[Dict[str, Any]] = None,
    handler: Optional[str] = None,
    agent: Optional[str] = None,
    project_name: Optional[str] = None,
    board: Optional[Dict[str, Any]] = None,
) -> str:
    """
    One Discord-friendly status message: project, lane counts, current work.
    Capped ~1800 chars for webhook content limits.
    """
    name = (project_name or getattr(state, "PROJECT_NAME", None) or "").strip()
    if not name:
        name = str(getattr(state, "CURRENT_PROJECT_ID", None) or "project")

    counts = _lane_counts(board)
    shown: List[str] = []
    seen: set[str] = set()

    def _include(ln: str) -> bool:
        if ln in _ALWAYS:
            return True
        if ln in _WHEN_PRESENT and ln in counts:
            return True
        if counts.get(ln, 0) > 0:
            return True
        return False

    for ln in list(_LANE_ORDER) + sorted(counts.keys()):
        if ln in seen or not _include(ln):
            continue
        seen.add(ln)
        shown.append(f"{ln}: {counts.get(ln, 0)}")

    lines: List[str] = [f"Project: {name}", "Lanes — " + " · ".join(shown)]

    needs_user = counts.get("Needs User", 0)
    if needs_user > 0:
        lines.append(f"Needs your input: {needs_user} card(s) in Needs User")

    if active_task and isinstance(active_task, dict):
        tid = str(active_task.get("id") or "?")
        title = str(active_task.get("title") or "?").strip() or "?"
        lane = str(active_task.get("status") or "")
        try:
            from backend.agents.task_context import get_task_lane

            lane = get_task_lane(tid) or lane or "?"
        except Exception:
            lane = lane or "?"
        who = (agent or handler or "agent").strip()
        lines.append(f"Working on: [{tid}] {title} ({lane} · {who})")
    elif handler == "idle":
        lines.append("Working on: (idle — no active card)")
    elif handler == "needs_user":
        lines.append("Working on: paused for Needs User")

    text = "\n".join(lines)
    if len(text) > 1800:
        text = text[:1799] + "…"
    return text


def notify_board_status_after_step(
    *,
    active_task: Optional[Dict[str, Any]] = None,
    handler: Optional[str] = None,
    agent: Optional[str] = None,
) -> None:
    """Send board digest if phone notify + board status toggle are on."""
    from backend.services.phone_notify import notify_if_enabled

    body = build_board_status_digest(
        active_task=active_task,
        handler=handler,
        agent=agent,
        project_name=getattr(state, "PROJECT_NAME", None),
    )
    # Dedup key = content hash so unchanged board skips spam within window
    digest_key = hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]
    notify_if_enabled(
        "board_status",
        "Board status",
        body,
        task_id=f"board:{digest_key}",
    )
