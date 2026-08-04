"""Structured server-side traces for LLM turns (debug workflow setting)."""

from __future__ import annotations

from typing import Any, Dict, Optional

from backend.services.workflow_settings import get_workflow_settings


def decision_trace_enabled() -> bool:
    return bool(get_workflow_settings().get("enableLlmDecisionTrace", False))


def model_rationale_enabled() -> bool:
    ws = get_workflow_settings()
    return bool(ws.get("enableLlmDecisionTrace")) and bool(ws.get("enableLlmModelRationale", False))


def build_decision_trace(
    *,
    outcome: str,
    detail: str,
    rejection: Optional[str] = None,
    tools_considered: Optional[list] = None,
) -> Dict[str, Any]:
    trace: Dict[str, Any] = {"outcome": outcome, "detail": detail}
    if rejection:
        trace["rejection"] = rejection
    if tools_considered:
        trace["toolsConsidered"] = tools_considered[:24]
    return trace


def rationale_step_suffix() -> str:
    if not model_rationale_enabled():
        return ""
    return (
        "\nDebug: start any assistant text with a single line "
        "`RATIONALE: <one sentence why you chose this tool or reply>`.\n"
    )
