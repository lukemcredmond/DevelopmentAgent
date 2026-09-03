"""Audit Done-lane cards for incomplete dev/QA evidence."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from backend.agents.task_context import normalize_task, record_task_decision
from backend.services.agent_work_items import derive_agent_work_items
from backend.services.board_service import move_board_stage
from backend.services.workflow_settings import get_workflow_settings

_DEV_PROGRESS_IDS = frozenset({"read:files", "write:implement", "verify:command"})


def _ac_unchecked(task: Dict[str, Any]) -> int:
    ws = get_workflow_settings()
    if not ws.get("requireAcChecklistForDone", True):
        return 0
    acs = [str(c).strip() for c in (task.get("acceptanceCriteria") or []) if str(c).strip()]
    if not acs:
        return 0
    checks = task.get("acChecklist")
    if not isinstance(checks, list):
        checks = []
    while len(checks) < len(acs):
        checks.append(False)
    checks = [bool(x) for x in checks[: len(acs)]]
    return sum(1 for c in checks if not c)


def audit_single_done_task(task: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Return audit row if incomplete, else None."""
    normalize_task(task)
    if task.get("splitSuperseded"):
        return None
    items = derive_agent_work_items(task)
    pending_dev = [
        str(i.get("label") or i["id"])
        for i in items
        if isinstance(i, dict)
        and i.get("id") in _DEV_PROGRESS_IDS
        and str(i.get("status") or "") == "pending"
    ]
    blocked_dev = [
        str(i.get("label") or i["id"])
        for i in items
        if isinstance(i, dict)
        and i.get("id") in _DEV_PROGRESS_IDS
        and str(i.get("status") or "") == "blocked"
    ]
    unchecked_ac = _ac_unchecked(task)
    reasons: List[str] = []
    if pending_dev:
        reasons.append(f"Agent progress pending: {', '.join(pending_dev)}")
    if blocked_dev:
        reasons.append(f"Agent progress blocked: {', '.join(blocked_dev)}")
    if unchecked_ac:
        ac_total = len([c for c in (task.get("acceptanceCriteria") or []) if str(c).strip()])
        reasons.append(f"Acceptance criteria unchecked ({unchecked_ac}/{ac_total})")

    if not reasons:
        return None
    return {
        "taskId": str(task.get("id") or ""),
        "title": str(task.get("title") or ""),
        "reasons": reasons,
        "pendingDevLabels": pending_dev,
        "blockedDevLabels": blocked_dev,
        "uncheckedAcCount": unchecked_ac,
    }


def audit_done_tasks(board: Optional[Dict[str, List[Dict[str, Any]]]] = None) -> Dict[str, Any]:
    from backend import state

    board = board if board is not None else state.SHARED_BOARD
    done_tasks = list(board.get("Done") or [])
    items: List[Dict[str, Any]] = []
    for task in done_tasks:
        if not isinstance(task, dict):
            continue
        row = audit_single_done_task(task)
        if row:
            items.append(row)
    return {
        "totalDone": len(done_tasks),
        "incompleteCount": len(items),
        "completeCount": len(done_tasks) - len(items),
        "items": items,
    }


def apply_done_audit_actions(
    task_ids: List[str],
    target_lane: str,
    *,
    only_incomplete: bool = True,
) -> Dict[str, Any]:
    target = str(target_lane or "").strip()
    if target not in ("In Progress", "Backlog"):
        return {"ok": False, "error": "moveTo must be 'In Progress' or 'Backlog'", "moved": []}
    from backend import state
    from backend.agents.task_context import find_task_by_id

    moved: List[str] = []
    skipped: List[str] = []
    audit = audit_done_tasks()
    incomplete_ids = {str(i["taskId"]) for i in audit.get("items") or [] if i.get("taskId")}

    for tid in task_ids:
        needle = str(tid or "").strip()
        if not needle:
            continue
        task = find_task_by_id(needle)
        if not task:
            skipped.append(needle)
            continue
        if str(task.get("status") or "") != "Done":
            skipped.append(needle)
            continue
        if only_incomplete and needle not in incomplete_ids:
            skipped.append(needle)
            continue
        result = move_board_stage(needle, target)
        if result.startswith("Error"):
            skipped.append(needle)
            continue
        record_task_decision(
            needle,
            "User",
            "done_audit",
            f"Done audit: moved to {target} — incomplete evidence",
        )
        moved.append(needle)

    if moved:
        from backend.services.project_service import save_current_project_state

        save_current_project_state(force_board=True)

    return {"ok": True, "moved": moved, "skipped": skipped, "targetLane": target}
