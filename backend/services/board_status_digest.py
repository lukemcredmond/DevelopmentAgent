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
    "Blocked",
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
_WHEN_PRESENT = frozenset({"Pending Approval", "Code Review", "Blocked"})


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
    include_operator_extras: bool = False,
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
        if include_operator_extras:
            nu_ids: List[str] = []
            for t in (board or state.SHARED_BOARD).get("Needs User", []) or []:
                if isinstance(t, dict) and t.get("id"):
                    nu_ids.append(str(t["id"]))
                if len(nu_ids) >= 5:
                    break
            if nu_ids:
                lines.append("Needs User ids: " + ", ".join(nu_ids))

    blocked = counts.get("Blocked", 0)
    if blocked > 0:
        lines.append(f"Blocked: {blocked} card(s) waiting on dependencies")

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
        # Richer active-card line: stuck / stop / backup remaining
        extras: List[str] = []
        stuck = active_task.get("stuckLoops")
        if stuck not in (None, 0, "0"):
            extras.append(f"stuckLoops={stuck}")
        outcome = active_task.get("lastStepOutcome")
        if isinstance(outcome, dict):
            stop = outcome.get("stopReason") or outcome.get("exitReason")
            if stop:
                extras.append(f"stop={stop}")
        rem = active_task.get("backupModelStepsRemaining")
        if isinstance(rem, dict):
            left = sum(max(0, int(v or 0)) for v in rem.values() if v is not None)
            if left > 0:
                extras.append(f"backupRemaining={left}")
        elif isinstance(rem, (int, float)) and rem > 0:
            extras.append(f"backupRemaining={int(rem)}")
        if extras:
            lines.append("Active card: " + " · ".join(extras))
    elif handler == "idle":
        lines.append("Working on: (idle — no active card)")
    elif handler == "needs_user":
        lines.append("Working on: paused for Needs User")

    if include_operator_extras:
        cancel = bool(getattr(state, "SPRINT_CANCEL", False))
        intent = str(getattr(state, "SPRINT_CANCEL_INTENT", None) or "") or "none"
        lines.append(f"Sprint: cancel={cancel} intent={intent}")

    text = "\n".join(lines)
    if len(text) > 1800:
        text = text[:1799] + "…"
    return text


def board_status_fingerprint(
    *,
    active_task: Optional[Dict[str, Any]] = None,
    board: Optional[Dict[str, Any]] = None,
) -> str:
    """Stable key for digest notify: lane counts + Needs User/Blocked + active id."""
    counts = _lane_counts(board)
    parts = [f"{k}:{counts.get(k, 0)}" for k in _LANE_ORDER if k in counts or k in _ALWAYS]
    active_id = ""
    if active_task and isinstance(active_task, dict):
        active_id = str(active_task.get("id") or "")
    raw = "|".join(parts) + f"|active={active_id}|nu={counts.get('Needs User', 0)}|bl={counts.get('Blocked', 0)}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


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
    # Prefer structural fingerprint so unchanged lane/active board skips spam
    digest_key = board_status_fingerprint(active_task=active_task)
    notify_if_enabled(
        "board_status",
        "Board status",
        body,
        task_id=f"board:{digest_key}",
    )
