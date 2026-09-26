"""Implementer execution profile helpers (Cursor-like single-agent mode)."""

from __future__ import annotations

from typing import Any, Dict, Optional

from backend import state
from backend.services.workflow_settings import get_execution_profile, get_workflow_settings


def is_auto_sprint_active() -> bool:
    return bool(getattr(state, "AUTO_SPRINT_ACTIVE", False))


def implementer_gate_relaxation_active(ws: Optional[Dict[str, Any]] = None) -> bool:
    """Soften delivery gates when implementer profile is active."""
    if ws is None:
        ws = get_workflow_settings()
    if get_execution_profile(ws) != "implementer":
        return False
    if not ws.get("implementerRelaxGatesOnAutoSprint", True):
        return False
    if ws.get("implementerRelaxGatesAlways", True):
        return True
    return is_auto_sprint_active() or bool(ws.get("autonomousMode"))


def resolve_plan_run_execution_profile(ws: Optional[Dict[str, Any]] = None) -> str:
    """Profile to use for Plan & Run (implementer | scrum | inherit current)."""
    if ws is None:
        ws = get_workflow_settings()
    raw = str(ws.get("planRunExecutionProfile") or "implementer").strip().lower()
    if raw in ("inherit", "current", "keep"):
        return get_execution_profile(ws)
    if raw == "implementer":
        return "implementer"
    return "scrum"


def implementer_needs_user_cap_hint(ws: Optional[Dict[str, Any]] = None) -> str:
    """UI copy: recommend higher Needs User cap for multi-card implementer sprints."""
    settings = ws or get_workflow_settings()
    cap = int(settings.get("maxNeedsUserPerSprint") or 2)
    if get_execution_profile(settings) != "implementer":
        return ""
    if cap >= 8:
        return ""
    return (
        "Implementer mode: raise max Needs User per sprint to at least 8 when running "
        "multi-card auto-sprint so stuck_loop / phase_cycle_cap escalations are not capped at 2."
    )
