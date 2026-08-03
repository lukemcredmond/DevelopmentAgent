"""Heuristic AC checklist updates after successful run_command."""

from __future__ import annotations

from typing import Any, Dict, List

from backend.agents.task_context import find_task_by_id, normalize_task
from backend.services.card_delivery import sync_ac_verification, update_ac_verification_from_checklist
from backend.services.workflow_settings import get_workflow_settings


def ac_criterion_matches_command_success(criterion: str, command: str) -> bool:
    crit = str(criterion or "").lower()
    cmd = str(command or "").lower().strip()
    if not cmd:
        return False
    command_markers = (
        "command executed",
        "executed successfully",
        "run successfully",
        "command succeeds",
        "command completed",
    )
    if any(m in crit for m in command_markers):
        return True
    tokens = [t for t in cmd.split() if len(t) > 3]
    if tokens and any(t in crit for t in tokens):
        return True
    if "build" in crit and any(x in cmd for x in ("build", "analyze", "test", "run")):
        return "complete" in crit or "without error" in crit or "success" in crit
    return False


def maybe_tick_ac_for_run_command(
    task: Dict[str, Any],
    command: str,
    *,
    success: bool,
) -> List[int]:
    """Auto-check AC rows that match a successful command when checklist is required."""
    if not success:
        return []
    if not get_workflow_settings().get("requireAcChecklistForDone", True):
        return []
    normalize_task(task)
    acs = task.get("acceptanceCriteria") or []
    if not acs:
        return []
    sync_ac_verification(task)
    checks = list(task.get("acChecklist") or [])
    while len(checks) < len(acs):
        checks.append(False)
    ticked: List[int] = []
    for i, ac in enumerate(acs):
        if checks[i]:
            continue
        if ac_criterion_matches_command_success(str(ac), command):
            checks[i] = True
            ticked.append(i)
    if ticked:
        task["acChecklist"] = checks[: len(acs)]
        update_ac_verification_from_checklist(
            task,
            note=f"Auto-checked after successful run_command: {command[:120]}",
        )
    return ticked


def apply_run_command_ac_ticks(task_id: str, command: str, *, success: bool) -> List[int]:
    task = find_task_by_id(task_id)
    if not task:
        return []
    return maybe_tick_ac_for_run_command(task, command, success=success)
