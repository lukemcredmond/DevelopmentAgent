"""Temporary per-agent backup model when stuck in a reasoning/tool-use loop."""

from __future__ import annotations

from typing import Any, Dict, Optional

from backend import state
from backend.agents.task_context import normalize_task, record_task_decision
from backend.services.logs import add_system_log
from backend.services.needs_user_guard import stuck_is_tool_or_lint
from backend.services.workflow_settings import get_workflow_settings

AGENT_KEYS = ("po", "dev", "cr", "qa")

_ROLE_LABEL = {
    "po": "Product Owner",
    "dev": "Developer",
    "cr": "Code Reviewer",
    "qa": "QA Tester",
}

_ARM_EXIT_REASONS = frozenset(
    {
        "read_only_no_edits",
        "plan_exhausted",
        "max_iterations",
        "text_only",
        "no_writes",
    }
)


def _remaining_map(task: Dict[str, Any]) -> Dict[str, int]:
    raw = task.get("backupModelStepsRemaining")
    if not isinstance(raw, dict):
        raw = {}
        task["backupModelStepsRemaining"] = raw
    out: Dict[str, int] = {}
    for key in AGENT_KEYS:
        try:
            out[key] = max(0, int(raw.get(key) or 0))
        except (TypeError, ValueError):
            out[key] = 0
    task["backupModelStepsRemaining"] = out
    return out


def primary_model(agent_key: str) -> str:
    return str((getattr(state, "PRIMARY_MODELS", {}) or {}).get(agent_key) or "").strip()


def backup_model(agent_key: str) -> str:
    return str((getattr(state, "BACKUP_MODELS", {}) or {}).get(agent_key) or "").strip()


def clear_backup_remaining(task: Dict[str, Any], agent_key: Optional[str] = None) -> None:
    rem = _remaining_map(task)
    if agent_key:
        rem[agent_key] = 0
    else:
        for k in AGENT_KEYS:
            rem[k] = 0
    task["backupModelStepsRemaining"] = rem


def restore_primary_model(agent, agent_key: str) -> str:
    """Set agent.model back to the configured primary for this role."""
    primary = primary_model(agent_key)
    if primary:
        agent.model = primary
    return str(getattr(agent, "model", "") or "")


def arm_backup_for_agent(
    agent_key: str,
    task: Dict[str, Any],
    *,
    reason: str = "",
) -> bool:
    """
    Arm N backup steps for this agent on this card.
    Returns True if armed (or already armed with remaining > 0).
    """
    agent_key = str(agent_key or "").lower()
    if agent_key not in AGENT_KEYS:
        return False

    ws = get_workflow_settings()
    if not ws.get("enableBackupModelOnStuck", True):
        return False

    backup = backup_model(agent_key)
    primary = primary_model(agent_key)
    if not backup or backup == primary:
        return False

    normalize_task(task)
    if stuck_is_tool_or_lint(task):
        return False

    rem = _remaining_map(task)
    if rem.get(agent_key, 0) > 0:
        return True  # already armed

    steps = max(1, int(ws.get("backupModelStuckSteps", 2)))
    rem[agent_key] = steps
    task["backupModelStepsRemaining"] = rem

    task_id = str(task.get("id") or "")
    label = _ROLE_LABEL.get(agent_key, agent_key)
    detail = reason or "stuck loop"
    record_task_decision(
        task_id,
        "System",
        "backup_model",
        f"Armed {label} backup model '{backup}' for {steps} step(s)",
        detail,
    )
    add_system_log(
        "System",
        "info",
        f"{task_id}: backup model armed for {label} → {backup} ({steps} step(s); {detail})",
    )
    return True


def should_arm_from_exit_reason(exit_reason: Optional[str]) -> bool:
    if not exit_reason:
        return False
    return str(exit_reason).lower() in _ARM_EXIT_REASONS


def apply_model_for_step(agent, agent_key: str, task: Optional[Dict[str, Any]]) -> str:
    """
    Apply backup model for this step if remaining > 0; otherwise restore primary.
    Decrements remaining when backup is used. Returns the model name in use.
    """
    agent_key = str(agent_key or "").lower()
    primary = primary_model(agent_key)
    if not task or agent_key not in AGENT_KEYS:
        if primary:
            agent.model = primary
        return str(getattr(agent, "model", "") or primary)

    rem = _remaining_map(task)
    left = rem.get(agent_key, 0)
    backup = backup_model(agent_key)

    if left > 0 and backup and backup != primary:
        agent.model = backup
        rem[agent_key] = left - 1
        task["backupModelStepsRemaining"] = rem
        label = _ROLE_LABEL.get(agent_key, agent_key)
        add_system_log(
            "System",
            "info",
            f"Using backup model {backup} for {label} ({rem[agent_key]} left after this step)",
        )
        return backup

    restore_primary_model(agent, agent_key)
    return str(getattr(agent, "model", "") or primary)
