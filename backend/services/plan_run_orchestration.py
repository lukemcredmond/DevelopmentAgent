"""Plan & Run profile selection and lightweight backlog bootstrap."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from backend import state
from backend.agents.task_context import (
    count_claimable_backlog_tasks,
    init_new_task,
    normalize_task,
)
from backend.services.implementer_profile import resolve_plan_run_execution_profile
from backend.services.logs import add_system_log
from backend.services.workflow_settings import get_execution_profile, save_workflow_settings


_ACTIONABLE_VERBS = re.compile(
    r"\b(add|implement|fix|build|create|update|refactor|remove|migrate|wire|hook)\b",
    re.I,
)
_AC_LINE = re.compile(r"^\s*(?:[-*]|\d+[.)])\s+(.+)$", re.M)


def brief_is_actionable(brief: str, *, min_chars: int = 40) -> bool:
    text = (brief or "").strip()
    if len(text) < min_chars:
        return False
    if _ACTIONABLE_VERBS.search(text):
        return True
    ac_matches = _AC_LINE.findall(text)
    if len(ac_matches) >= 2:
        return True
    if "acceptance criteria" in text.lower() and len(ac_matches) >= 1:
        return True
    return False


def has_dev_ready_backlog() -> bool:
    if count_claimable_backlog_tasks() > 0:
        return True
    for lane in ("Backlog", "In Progress"):
        for task in state.SHARED_BOARD.get(lane, []) or []:
            if not isinstance(task, dict):
                continue
            normalize_task(task)
            if task.get("requiresDev", True) and task.get("workType") != "planning":
                return True
    return False


def should_skip_po_plan_for_plan_run(brief: str, ws: Dict[str, Any] | None = None) -> bool:
    from backend.services.workflow_settings import get_workflow_settings

    if ws is None:
        ws = get_workflow_settings()
    profile = resolve_plan_run_execution_profile(ws)
    if profile != "implementer":
        return False
    if not ws.get("planRunSkipPoWhenActionable", True):
        return False
    if has_dev_ready_backlog():
        return True
    return brief_is_actionable(brief)


def ensure_implementer_card_from_brief(brief: str) -> Tuple[bool, str]:
    """Create one dev-ready Backlog card when implementer Plan & Run skips PO."""
    if has_dev_ready_backlog():
        return True, "existing backlog"
    text = (brief or "").strip()
    if not text:
        return False, "empty brief"
    ac: List[str] = []
    for match in _AC_LINE.findall(text):
        line = match.strip()
        if line and len(line) > 3:
            ac.append(line[:240])
    if not ac:
        ac = ["Implementation matches the brief", "Project lint/test passes for touched files"]
    ac = ac[:3]
    title = text.split("\n", 1)[0].strip()[:120] or "Implement brief"
    task = init_new_task(
        {
            "title": title,
            "description": text[:4000],
            "acceptanceCriteria": ac,
            "requiresDev": True,
            "workType": "implementation",
            "priority": 10,
        }
    )
    normalize_task(task)
    state.SHARED_BOARD.setdefault("Backlog", []).append(task)
    add_system_log(
        "System",
        "info",
        f"Plan & Run: created implementer card '{task.get('id')}' from actionable brief (skipped PO plan)",
    )
    return True, str(task.get("id") or "")


def apply_plan_run_execution_profile(
    *,
    override: Optional[str] = None,
    persist: bool = True,
) -> str:
    """Set execution profile for Plan & Run; returns profile applied."""
    from backend.services.workflow_settings import get_workflow_settings

    ws = get_workflow_settings()
    if override and str(override).strip().lower() in ("implementer", "scrum"):
        profile = str(override).strip().lower()
    else:
        profile = resolve_plan_run_execution_profile(ws)
    if persist and get_execution_profile(ws) != profile:
        save_workflow_settings({"executionProfile": profile})
        add_system_log("System", "info", f"Plan & Run: execution profile → {profile}")
    return profile
