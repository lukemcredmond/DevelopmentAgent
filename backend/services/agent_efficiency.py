"""Local-Ollama agent efficiency: lean prompts, phase model routing, per-turn tool caps."""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

EFFICIENCY_STANDARD = "standard"
EFFICIENCY_HIGH = "high"


def get_efficiency_mode(ws: Optional[Dict[str, Any]] = None) -> str:
    if ws is None:
        from backend.services.workflow_settings import get_workflow_settings

        ws = get_workflow_settings()
    # Partial override dicts without the key should not force high (honor promptProfile).
    # Live settings always include the key via DEFAULT_WORKFLOW_SETTINGS merge.
    if "agentEfficiencyMode" not in ws:
        return EFFICIENCY_STANDARD
    raw = str(ws.get("agentEfficiencyMode") or EFFICIENCY_HIGH).strip().lower()
    if raw in (EFFICIENCY_STANDARD, "full", "normal"):
        return EFFICIENCY_STANDARD
    return EFFICIENCY_HIGH


def efficiency_high(ws: Optional[Dict[str, Any]] = None) -> bool:
    return get_efficiency_mode(ws) == EFFICIENCY_HIGH


def phase_model_routing_enabled(ws: Optional[Dict[str, Any]] = None) -> bool:
    if ws is None:
        from backend.services.workflow_settings import get_workflow_settings

        ws = get_workflow_settings()
    if "enablePhaseModelRouting" in ws:
        return bool(ws.get("enablePhaseModelRouting"))
    return efficiency_high(ws)


def resolve_step_model(
    *,
    role: str,
    phase: Optional[str],
    primary_model: str,
    backup_model: Optional[str] = None,
    ws: Optional[Dict[str, Any]] = None,
) -> Tuple[str, str]:
    """
    Return (model_name, reason) for this LLM turn.
    Thin hook for future cloud providers — same signature.
    """
    if ws is None:
        from backend.services.workflow_settings import get_workflow_settings

        ws = get_workflow_settings()
    primary = (primary_model or "").strip() or "qwen2.5-coder:14b"
    if role != "Developer" or not phase_model_routing_enabled(ws):
        return primary, "role_primary"

    explore = str(ws.get("devExploreModel") or "").strip()
    if not explore:
        explore = (backup_model or "").strip() or str(ws.get("discordModelPresetFast") or "").strip()
    if not explore:
        explore = "qwen2.5-coder:7b"

    patch = str(ws.get("devPatchModel") or "").strip() or primary

    ph = str(phase or "explore").strip().lower()
    if ph in ("explore", ""):
        return explore, "phase_explore"
    # patch / verify / done / stuck → stronger coder (keep warm for verify)
    return patch, f"phase_{ph or 'patch'}"


def max_tools_per_llm_turn(*, phase: Optional[str] = None, ws: Optional[Dict[str, Any]] = None) -> int:
    if ws is None:
        from backend.services.workflow_settings import get_workflow_settings

        ws = get_workflow_settings()
    ph = str(phase or "explore").strip().lower()
    if efficiency_high(ws):
        base = max(1, int(ws.get("maxToolsPerLlmTurn") or 3))
        if ph in ("patch", "verify", "done"):
            return min(2, base)
        return base
    if ws.get("maxToolsPerLlmTurn") not in (None, ""):
        return max(1, int(ws.get("maxToolsPerLlmTurn") or 3))
    return 12


def apply_tool_turn_cap(
    calls: list,
    *,
    phase: Optional[str] = None,
    ws: Optional[Dict[str, Any]] = None,
) -> Tuple[list, list, int]:
    """
    Soft-cap tool calls in one assistant message.
    Returns (to_execute, deferred, cap).
    """
    cap = max_tools_per_llm_turn(phase=phase, ws=ws)
    if len(calls) <= cap:
        return list(calls), [], cap
    return list(calls[:cap]), list(calls[cap:]), cap


def effective_max_llm_iterations(ws: Optional[Dict[str, Any]] = None) -> int:
    if ws is None:
        from backend.services.workflow_settings import get_workflow_settings

        ws = get_workflow_settings()
    return max(1, int(ws.get("maxLlmIterationsPerStep") or 8))


def effective_max_tool_failures(ws: Optional[Dict[str, Any]] = None) -> int:
    if ws is None:
        from backend.services.workflow_settings import get_workflow_settings

        ws = get_workflow_settings()
    return max(1, int(ws.get("maxToolFailuresPerStep") or 5))


def should_throttle_step_recap(
    *,
    tool_batch_index: int,
    phase_graph_active: bool,
    ws: Optional[Dict[str, Any]] = None,
) -> bool:
    """
    Return True if this batch should SKIP appending a step recap.
    High mode: at most every 2nd batch; skip entirely when phase graph is on.
    """
    if ws is None:
        from backend.services.workflow_settings import get_workflow_settings

        ws = get_workflow_settings()
    if not efficiency_high(ws):
        return False
    if phase_graph_active and bool(ws.get("enableDevPhaseGraph", True)):
        return True
    # 1-based batch index: allow 2, 4, 6...
    return int(tool_batch_index or 0) % 2 != 0
