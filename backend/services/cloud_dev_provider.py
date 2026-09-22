"""Optional frontier/cloud LLM for Developer implementer and stuck-recovery steps."""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from backend import state
from backend.agents.task_context import normalize_task, record_task_decision
from backend.services.implementer_profile import implementer_gate_relaxation_active, is_auto_sprint_active
from backend.services.logs import add_system_log
from backend.services.workflow_settings import get_execution_profile, get_workflow_settings

AGENT_KEYS = ("dev",)


def cloud_dev_enabled(ws: Optional[Dict[str, Any]] = None) -> bool:
    if ws is None:
        ws = get_workflow_settings()
    if not ws.get("enableCloudDevProvider", False):
        return False
    model = str(ws.get("cloudDevModel") or "").strip()
    url = str(ws.get("cloudDevBaseUrl") or "").strip()
    return bool(model and url)


def cloud_dev_config(ws: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    if not cloud_dev_enabled(ws):
        return None
    if ws is None:
        ws = get_workflow_settings()
    return {
        "provider": "openai_compat",
        "baseUrl": str(ws.get("cloudDevBaseUrl") or "").strip().rstrip("/"),
        "apiKey": str(ws.get("cloudDevApiKey") or "").strip(),
        "timeoutSec": float(ws.get("cloudDevRequestTimeoutSec") or ws.get("ollamaRequestTimeoutSec") or 900),
        "model": str(ws.get("cloudDevModel") or "").strip(),
    }


def should_use_cloud_dev_for_step(*, agent_key: str, ws: Optional[Dict[str, Any]] = None) -> bool:
    if agent_key != "dev" or not cloud_dev_enabled(ws):
        return False
    if ws is None:
        ws = get_workflow_settings()
    use_for = str(ws.get("cloudDevUseFor") or "implementer_autonomous").strip().lower()
    if use_for in ("always", "all", "on"):
        return True
    if use_for in ("stuck_only", "stuck"):
        return False
    # implementer_autonomous: implementer profile during auto sprint / relaxed autonomous
    if get_execution_profile(ws) != "implementer":
        return False
    return is_auto_sprint_active() or implementer_gate_relaxation_active(ws)


def _remaining_map(task: Dict[str, Any]) -> Dict[str, int]:
    raw = task.get("cloudModelStepsRemaining")
    if not isinstance(raw, dict):
        raw = {}
        task["cloudModelStepsRemaining"] = raw
    out: Dict[str, int] = {}
    for key in AGENT_KEYS:
        try:
            out[key] = max(0, int(raw.get(key) or 0))
        except (TypeError, ValueError):
            out[key] = 0
    task["cloudModelStepsRemaining"] = out
    return out


def arm_cloud_for_agent(
    agent_key: str,
    task: Dict[str, Any],
    *,
    reason: str = "",
) -> bool:
    agent_key = str(agent_key or "").lower()
    if agent_key not in AGENT_KEYS or not cloud_dev_enabled():
        return False
    normalize_task(task)
    rem = _remaining_map(task)
    if rem.get(agent_key, 0) > 0:
        return True
    ws = get_workflow_settings()
    steps = max(1, int(ws.get("cloudDevStuckSteps") or 1))
    rem[agent_key] = steps
    task["cloudModelStepsRemaining"] = rem
    cfg = cloud_dev_config(ws) or {}
    model = str(cfg.get("model") or "")
    task_id = str(task.get("id") or "")
    record_task_decision(
        task_id,
        "System",
        "cloud_model",
        f"Armed cloud Dev model '{model}' for {steps} step(s)",
        reason or "stuck recovery",
    )
    add_system_log(
        "System",
        "info",
        f"{task_id}: cloud Dev model armed → {model} ({steps} step(s); {reason or 'stuck recovery'})",
    )
    return True


def apply_cloud_dev_for_step(agent, agent_key: str, task: Optional[Dict[str, Any]]) -> Tuple[bool, str]:
    """
    When cloud is armed or policy says use cloud, set agent model + provider override.
    Returns (using_cloud, model_name).
    """
    agent_key = str(agent_key or "").lower()
    ws = get_workflow_settings()
    cfg = cloud_dev_config(ws)
    if not cfg:
        setattr(agent, "_use_cloud_provider", False)
        return False, str(getattr(agent, "model", "") or "")

    use_cloud = should_use_cloud_dev_for_step(agent_key=agent_key, ws=ws)
    if task and agent_key in AGENT_KEYS:
        rem = _remaining_map(task)
        if rem.get(agent_key, 0) > 0:
            use_cloud = True

    if not use_cloud:
        setattr(agent, "_use_cloud_provider", False)
        return False, str(getattr(agent, "model", "") or "")

    model = str(cfg.get("model") or "")
    if model:
        agent.model = model
    setattr(agent, "_use_cloud_provider", True)
    setattr(agent, "_cloud_provider_config", dict(cfg))

    if task and agent_key in AGENT_KEYS:
        rem = _remaining_map(task)
        left = rem.get(agent_key, 0)
        if left > 0:
            rem[agent_key] = left - 1
            task["cloudModelStepsRemaining"] = rem
            add_system_log(
                "System",
                "info",
                f"Using cloud Dev model {model} ({rem[agent_key]} left after this step)",
            )
    return True, model


def clear_cloud_remaining(task: Dict[str, Any], agent_key: Optional[str] = None) -> None:
    rem = _remaining_map(task)
    if agent_key:
        rem[str(agent_key).lower()] = 0
    else:
        for key in AGENT_KEYS:
            rem[key] = 0
    task["cloudModelStepsRemaining"] = rem
