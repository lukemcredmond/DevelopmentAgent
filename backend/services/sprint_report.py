"""Lane-move ledger for the current sprint and recent finished reports."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, List, Optional

from backend import state

REPORTS_CAP = 20
MAX_MOVES = 400


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _reports_key(project_id: str | None = None) -> str:
    pid = project_id or state.CURRENT_PROJECT_ID
    return f"sprint_reports:{pid}"


def lane_counts(board: Optional[Dict[str, Any]] = None) -> Dict[str, int]:
    source = board if board is not None else state.SHARED_BOARD
    counts: Dict[str, int] = {}
    for lane, tasks in (source or {}).items():
        counts[str(lane)] = len(tasks or [])
    return counts


def _empty_report() -> Dict[str, Any]:
    return {
        "startedAt": _now(),
        "endedAt": None,
        "status": "running",
        "stepsRun": 0,
        "moves": [],
        "movedCount": 0,
        "byTransition": {},
        "unblocked": 0,
        "splitParents": 0,
        "needsUserIn": 0,
        "needsUserOut": 0,
        "laneCountsStart": lane_counts(),
        "laneCountsEnd": lane_counts(),
    }


def apply_rollups(report: Dict[str, Any]) -> Dict[str, Any]:
    moves = [m for m in (report.get("moves") or []) if isinstance(m, dict)]
    by: Dict[str, int] = {}
    unblocked = 0
    split_parents = 0
    needs_in = 0
    needs_out = 0
    for move in moves:
        from_lane = str(move.get("fromLane") or "")
        to_lane = str(move.get("toLane") or "")
        key = f"{from_lane} → {to_lane}"
        by[key] = int(by.get(key) or 0) + 1
        if from_lane == "Blocked" and to_lane != "Blocked":
            unblocked += 1
        source = str(move.get("source") or "")
        if source == "split" or move.get("splitSuperseded"):
            split_parents += 1
        if to_lane == "Needs User":
            needs_in += 1
        if from_lane == "Needs User" and to_lane != "Needs User":
            needs_out += 1
    report["movedCount"] = len(moves)
    report["byTransition"] = by
    report["unblocked"] = unblocked
    report["splitParents"] = split_parents
    report["needsUserIn"] = needs_in
    report["needsUserOut"] = needs_out
    if report.get("status") == "running":
        report["laneCountsEnd"] = lane_counts()
    return report


def begin_sprint_report(*, restart: bool = True) -> Dict[str, Any]:
    if not restart and getattr(state, "CURRENT_SPRINT_REPORT", None):
        return apply_rollups(dict(state.CURRENT_SPRINT_REPORT))
    report = _empty_report()
    state.CURRENT_SPRINT_REPORT = report
    return report


def record_lane_move(
    task_id: str,
    title: str,
    from_lane: str,
    to_lane: str,
    source: str = "move",
    *,
    split_superseded: bool = False,
) -> None:
    if not from_lane or not to_lane or from_lane == to_lane:
        return
    report = getattr(state, "CURRENT_SPRINT_REPORT", None)
    if not isinstance(report, dict):
        return
    moves = list(report.get("moves") or [])
    if len(moves) >= MAX_MOVES:
        return
    moves.append(
        {
            "taskId": str(task_id),
            "title": str(title or task_id)[:200],
            "fromLane": str(from_lane),
            "toLane": str(to_lane),
            "source": str(source or "move"),
            "splitSuperseded": bool(split_superseded),
            "at": _now(),
        }
    )
    report["moves"] = moves
    apply_rollups(report)


def current_sprint_report() -> Optional[Dict[str, Any]]:
    report = getattr(state, "CURRENT_SPRINT_REPORT", None)
    if not isinstance(report, dict):
        return None
    return apply_rollups(dict(report))


def list_sprint_reports(project_id: str | None = None) -> List[Dict[str, Any]]:
    raw = state.storage.get_setting(_reports_key(project_id))
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    return [item for item in data if isinstance(item, dict)]


def _persist_reports(reports: List[Dict[str, Any]], project_id: str | None = None) -> None:
    state.storage.set_setting(_reports_key(project_id), json.dumps(reports[:REPORTS_CAP]))


def finalize_sprint_report(steps: int, status: str = "completed") -> Dict[str, Any]:
    report = getattr(state, "CURRENT_SPRINT_REPORT", None)
    if not isinstance(report, dict):
        return {}
    report["endedAt"] = _now()
    report["status"] = str(status or "completed")
    report["stepsRun"] = int(steps or 0)
    report["laneCountsEnd"] = lane_counts()
    apply_rollups(report)
    finished = dict(report)
    history = [finished, *list_sprint_reports()]
    _persist_reports(history[:REPORTS_CAP])
    state.CURRENT_SPRINT_REPORT = None
    return finished
