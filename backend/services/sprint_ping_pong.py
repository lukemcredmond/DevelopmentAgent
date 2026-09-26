"""Detect In Progress ↔ Needs User ping-pong and deprioritize chronic churners."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from backend import state

PING_PONG_DEPRIORITIZE_THRESHOLD = 2
PING_PONG_DEPRIORITIZE_STEP_OFFSET = 8
MAX_MOVES_SCAN = 400
PING_PONG_DISPLAY_CAP = 10

LANE_IP = "In Progress"
LANE_NU = "Needs User"
CLEAR_DEPRIORITIZE_LANES = frozenset({"QA", "Done", "Code Review"})


def _transition_key(from_lane: str, to_lane: str) -> str:
    return f"{from_lane} → {to_lane}"


def analyze_task_moves(moves: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Per-task stats from ordered lane moves."""
    ordered = sorted(
        [m for m in moves if isinstance(m, dict)],
        key=lambda m: str(m.get("at") or ""),
    )
    by_transition: Dict[str, int] = {}
    round_trips = 0
    pending_ip_to_nu = False
    nu_in = 0
    nu_out = 0
    last_at = ""
    last_transition = ""
    title = ""

    for move in ordered:
        from_lane = str(move.get("fromLane") or "")
        to_lane = str(move.get("toLane") or "")
        if not from_lane or not to_lane:
            continue
        title = str(move.get("title") or title)
        key = _transition_key(from_lane, to_lane)
        by_transition[key] = int(by_transition.get(key) or 0) + 1
        last_at = str(move.get("at") or last_at)
        last_transition = key

        if to_lane == LANE_NU:
            nu_in += 1
        if from_lane == LANE_NU and to_lane != LANE_NU:
            nu_out += 1

        if from_lane == LANE_IP and to_lane == LANE_NU:
            pending_ip_to_nu = True
        elif from_lane == LANE_NU and to_lane == LANE_IP:
            if pending_ip_to_nu:
                round_trips += 1
            pending_ip_to_nu = False

    score = min(PING_PONG_DISPLAY_CAP, round_trips)
    flag = round_trips >= PING_PONG_DEPRIORITIZE_THRESHOLD or (nu_in + nu_out >= 3)

    return {
        "moveCount": len(ordered),
        "byTransition": by_transition,
        "pingPongRoundTrips": round_trips,
        "pingPongScore": score,
        "pingPongFlag": flag,
        "needsUserIn": nu_in,
        "needsUserOut": nu_out,
        "lastMoveAt": last_at,
        "pingPongLastTransition": last_transition,
        "title": title,
    }


def group_moves_by_task(moves: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    by_task: Dict[str, List[Dict[str, Any]]] = {}
    for move in moves:
        if not isinstance(move, dict):
            continue
        tid = str(move.get("taskId") or "").strip()
        if not tid:
            continue
        by_task.setdefault(tid, []).append(move)
    return by_task


def collect_moves_for_analysis(
    *,
    project_id: Optional[str] = None,
    max_moves: int = MAX_MOVES_SCAN,
) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """Merge current + recent finalized report moves (newest reports first)."""
    from backend.services.sprint_report import current_sprint_report, list_sprint_reports

    chunks: List[List[Dict[str, Any]]] = []
    started_at: Optional[str] = None

    current = current_sprint_report()
    if isinstance(current, dict):
        started_at = str(current.get("startedAt") or "") or None
        chunks.append(list(current.get("moves") or []))

    for report in list_sprint_reports(project_id)[:2]:
        if not isinstance(report, dict):
            continue
        chunks.append(list(report.get("moves") or []))

    merged: List[Dict[str, Any]] = []
    for block in chunks:
        for move in block:
            if isinstance(move, dict):
                merged.append(move)
            if len(merged) >= max_moves:
                return merged, started_at
    return merged, started_at


def compute_ping_pong_by_task(
    moves: Optional[List[Dict[str, Any]]] = None,
    *,
    project_id: Optional[str] = None,
) -> Dict[str, Dict[str, Any]]:
    if moves is None:
        moves, _ = collect_moves_for_analysis(project_id=project_id)
    grouped = group_moves_by_task(moves)
    out: Dict[str, Dict[str, Any]] = {}
    for tid, task_moves in grouped.items():
        stats = analyze_task_moves(task_moves)
        stats["taskId"] = tid
        out[tid] = stats
    return out


def ping_pong_deprioritized_active(task: Dict[str, Any]) -> bool:
    until = task.get("pingPongDeprioritizedUntilStep")
    if until is None:
        return False
    try:
        from backend.services.needs_user_guard import current_sprint_step

        return current_sprint_step() < int(until)
    except (TypeError, ValueError):
        return False


def clear_ping_pong_deprioritize(task: Dict[str, Any]) -> None:
    task.pop("pingPongDeprioritizedUntilStep", None)


def maybe_clear_ping_pong_on_lane_change(task: Dict[str, Any], target_lane: str) -> None:
    if str(target_lane or "") in CLEAR_DEPRIORITIZE_LANES:
        clear_ping_pong_deprioritize(task)


def _apply_deprioritize_to_task(task: Dict[str, Any], stats: Dict[str, Any]) -> None:
    from backend.services.needs_user_guard import current_sprint_step

    task["pingPongRoundTrips"] = int(stats.get("pingPongRoundTrips") or 0)
    task["pingPongScore"] = int(stats.get("pingPongScore") or 0)
    last_tr = str(stats.get("pingPongLastTransition") or "").strip()
    if last_tr:
        task["pingPongLastTransition"] = last_tr

    if not stats.get("pingPongFlag"):
        return

    step = current_sprint_step()
    until = step + PING_PONG_DEPRIORITIZE_STEP_OFFSET
    prev_until = task.get("pingPongDeprioritizedUntilStep")
    if prev_until is not None:
        try:
            if int(prev_until) >= step:
                return
        except (TypeError, ValueError):
            pass

    task["pingPongDeprioritizedUntilStep"] = until
    try:
        from backend.agents.task_context import record_task_decision
        from backend.services.logs import add_system_log

        tid = str(task.get("id") or "")
        title = str(task.get("title") or tid)
        msg = (
            f"{title}: ping-pong deprioritized until sprint step {until} "
            f"({task['pingPongRoundTrips']} NU↔IP round-trips)"
        )
        add_system_log("System", "warning", msg[:300])
        if tid:
            record_task_decision(tid, "System", "ping_pong", msg[:400])
    except Exception:
        pass


def sync_ping_pong_to_board(ping_by_task: Dict[str, Dict[str, Any]]) -> None:
    from backend.agents.task_context import find_task_by_id

    for tid, stats in ping_by_task.items():
        live = find_task_by_id(tid)
        if not live:
            continue
        _apply_deprioritize_to_task(live, stats)


def refresh_ping_pong_from_report() -> Dict[str, Dict[str, Any]]:
    """Recompute pingPongByTask on current report and sync task fields."""
    report = getattr(state, "CURRENT_SPRINT_REPORT", None)
    if not isinstance(report, dict):
        return {}

    moves = list(report.get("moves") or [])
    ping_by_task = compute_ping_pong_by_task(moves)
    report["pingPongByTask"] = ping_by_task
    sync_ping_pong_to_board(ping_by_task)
    return ping_by_task


def build_move_summary_by_task(
    *,
    project_id: Optional[str] = None,
) -> Dict[str, Any]:
    moves, started_at = collect_moves_for_analysis(project_id=project_id)
    ping_by_task = compute_ping_pong_by_task(moves)
    tasks = sorted(
        ping_by_task.values(),
        key=lambda x: (
            -int(x.get("pingPongRoundTrips") or 0),
            -int(x.get("moveCount") or 0),
            str(x.get("taskId") or ""),
        ),
    )
    return {
        "currentSprintStartedAt": started_at,
        "movesScanned": len(moves),
        "tasks": tasks,
    }


def sort_dev_runnable(tasks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Deprioritize ping-pong and dev-deferred cards to the bottom of Dev picks.

    Sort key tiers: ping-pong deprioritized, then dev deferred, then priority, then task id.
    """
    from backend.services.dev_refusal_triage import dev_deferred_active

    def sort_key(t: Dict[str, Any]) -> Tuple[int, int, float, str]:
        deprio = 1 if ping_pong_deprioritized_active(t) else 0
        defer = 1 if dev_deferred_active(t) else 0
        pri = t.get("priority")
        if not isinstance(pri, (int, float)):
            pri = 100
        return (deprio, defer, float(pri), str(t.get("id") or ""))

    return sorted(tasks, key=sort_key)


def summarize_ping_pong_board(
    board: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    board = board if board is not None else state.SHARED_BOARD
    deprioritized: List[Dict[str, str]] = []
    for lane in ("In Progress", "Needs User", "Needs PO", "Backlog"):
        for task in board.get(lane) or []:
            if not isinstance(task, dict):
                continue
            if ping_pong_deprioritized_active(task):
                deprioritized.append(
                    {
                        "taskId": str(task.get("id") or ""),
                        "title": str(task.get("title") or "")[:120],
                    }
                )
    deprioritized.sort(key=lambda x: x["taskId"])
    return {
        "pingPongDeprioritizedCount": len(deprioritized),
        "pingPongDeprioritizedTasks": deprioritized[:5],
    }
