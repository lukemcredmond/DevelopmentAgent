"""Backlog readiness checks before auto-sprint (implementation vs planning-only)."""

from __future__ import annotations

from typing import Any, Dict, List

from backend import state


def _is_implementation_task(task: Dict[str, Any]) -> bool:
    if not isinstance(task, dict):
        return False
    work_type = str(task.get("workType") or "").strip().lower()
    if work_type == "planning":
        return False
    if task.get("requiresDev") is False:
        return False
    return True


def build_backlog_preflight() -> Dict[str, Any]:
    lanes = ("Backlog", "In Progress", "Refinement")
    implementation_ready = 0
    planning_only = 0
    fat_ac_cards: List[str] = []
    for lane in lanes:
        for task in state.SHARED_BOARD.get(lane) or []:
            if not isinstance(task, dict):
                continue
            tid = str(task.get("id") or "")
            if _is_implementation_task(task):
                implementation_ready += 1
            else:
                planning_only += 1
            acs = task.get("acceptanceCriteria") or []
            if isinstance(acs, list) and len(acs) > 3 and tid:
                fat_ac_cards.append(tid)

    warnings: List[str] = []
    if implementation_ready == 0 and planning_only > 0:
        warnings.append(
            "No implementation-ready cards in Backlog/In Progress — only planning or requiresDev=false cards. "
            "Dev will not write code until a small implementation card is In Progress."
        )
    if fat_ac_cards:
        warnings.append(
            f"{len(fat_ac_cards)} card(s) have more than 3 acceptance criteria — consider Split before Dev."
        )

    return {
        "implementationReady": implementation_ready,
        "planningOnly": planning_only,
        "fatAcTaskIds": fat_ac_cards[:12],
        "warnings": warnings,
    }


def log_backlog_preflight_warnings() -> None:
    from backend.services.logs import add_system_log

    pre = build_backlog_preflight()
    for msg in pre.get("warnings") or []:
        add_system_log("System", "warning", f"Backlog preflight: {msg}")
