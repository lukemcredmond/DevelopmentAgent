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

# Exit reasons that always force-arm even when lint/tool diagnostics are present.
_FORCE_ARM_EXIT_REASONS = frozenset(
    {
        "read_only_no_edits",
        "plan_exhausted",
        "max_iterations",
        "text_only",
        "no_writes",
        "duplicate_tool",
        "step_timeout",
        "tool_failure_stop",
    }
)

_ARM_EXIT_REASONS = _FORCE_ARM_EXIT_REASONS


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
        if hasattr(agent, "set_primary_model"):
            agent.set_primary_model(primary)
        else:
            agent.model = primary
    return str(getattr(agent, "model", "") or "")


def restore_all_primary_models(*, reason: str = "") -> Dict[str, str]:
    """Put every agent back on its configured primary model; returns what changed."""
    try:
        from backend.agents.registry import AGENT_MAP
    except Exception:
        return {}

    restored: Dict[str, str] = {}
    for key in AGENT_KEYS:
        agent = AGENT_MAP.get(key)
        if agent is None:
            continue
        primary = primary_model(key)
        if primary and str(getattr(agent, "model", "") or "") != primary:
            if hasattr(agent, "set_primary_model"):
                agent.set_primary_model(primary)
            else:
                agent.model = primary
            restored[key] = primary
    if restored:
        summary = ", ".join(f"{_ROLE_LABEL.get(k, k)} → {m}" for k, m in restored.items())
        add_system_log(
            "System",
            "info",
            f"Restored primary model: {summary}" + (f" ({reason})" if reason else ""),
        )
    return restored


def should_force_arm_from_exit_reason(exit_reason: Optional[str]) -> bool:
    if not exit_reason:
        return False
    return str(exit_reason).lower() in _FORCE_ARM_EXIT_REASONS


def _arm_skip_reason(
    agent_key: str,
    task: Dict[str, Any],
    *,
    force: bool,
) -> Optional[str]:
    """Return a human skip reason, or None if arming may proceed."""
    label = _ROLE_LABEL.get(agent_key, agent_key)
    ws = get_workflow_settings()
    if not ws.get("enableBackupModelOnStuck", True):
        return f"backup model not armed: enableBackupModelOnStuck is off"
    backup = backup_model(agent_key)
    primary = primary_model(agent_key)
    if not backup:
        return (
            f"backup model not armed: no {label} backup configured "
            f"(Settings → Models → Backup stuck)"
        )
    if backup == primary:
        return f"backup model not armed: {label} backup equals primary ({primary})"
    if not force and stuck_is_tool_or_lint(task):
        return (
            "backup model not armed: lint/tool wall "
            "(fix diagnostics or wait for agent-loop stop to force-arm)"
        )
    return None


def arm_backup_for_agent(
    agent_key: str,
    task: Dict[str, Any],
    *,
    reason: str = "",
    force: bool = False,
) -> bool:
    """
    Arm N backup steps for this agent on this card.
    Returns True if armed (or already armed with remaining > 0).

    When force=True, skip the lint/tool gate (used for agent-loop exit reasons).
    """
    agent_key = str(agent_key or "").lower()
    if agent_key not in AGENT_KEYS:
        return False

    normalize_task(task)
    skip = _arm_skip_reason(agent_key, task, force=force)
    if skip:
        task_id = str(task.get("id") or "")
        add_system_log(
            "System",
            "warning",
            f"{task_id}: {skip}" + (f" ({reason})" if reason else ""),
        )
        return False

    rem = _remaining_map(task)
    if rem.get(agent_key, 0) > 0:
        return True  # already armed

    ws = get_workflow_settings()
    steps = max(1, int(ws.get("backupModelStuckSteps", 2)))
    rem[agent_key] = steps
    task["backupModelStepsRemaining"] = rem

    backup = backup_model(agent_key)
    task_id = str(task.get("id") or "")
    label = _ROLE_LABEL.get(agent_key, agent_key)
    detail = reason or "stuck loop"
    if force and detail:
        detail = f"{detail} (force)"
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
    try:
        from backend.services.phone_notify import notify_if_enabled

        notify_if_enabled(
            "backup_armed",
            "Backup model armed",
            f"{task.get('title') or task_id}\n{label} → {backup} ({steps} step(s); {detail})",
            task_id=task_id,
        )
    except Exception:
        pass
    try:
        from backend.services.ollama_warmup import preload_backup_model_async

        preload_backup_model_async(backup, primary=primary_model(agent_key))
    except Exception:
        pass
    return True


def should_arm_from_exit_reason(exit_reason: Optional[str]) -> bool:
    if not exit_reason:
        return False
    return str(exit_reason).lower() in _ARM_EXIT_REASONS


def latest_loop_stop_exit_reason() -> Optional[str]:
    """Best-effort exit reason from LAST_STEP_OUTCOME / LAST_AGENT_STEP_RESULT."""
    outcome = state.LAST_STEP_OUTCOME
    if isinstance(outcome, dict):
        for key in ("exitReason", "stopReason"):
            val = outcome.get(key)
            if val and should_arm_from_exit_reason(str(val)):
                return str(val)
    result = state.LAST_AGENT_STEP_RESULT
    if isinstance(result, str) and result:
        from backend.services.step_diagnostics import derive_exit_reason

        derived = derive_exit_reason(
            agent_result=result,
            tools_used=None,
            lane_before="In Progress",
            lane_after="In Progress",
        )
        if should_arm_from_exit_reason(derived):
            return derived
    return None


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
        try:
            from backend.services.ollama_warmup import maybe_vram_unload_primary

            maybe_vram_unload_primary(primary, backup=backup)
        except Exception:
            pass
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
