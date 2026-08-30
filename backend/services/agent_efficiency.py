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
    if single_model_mode_active(ws):
        # Routing between an explore model and a patch model is pure reload cost when
        # only one model fits in VRAM at a time.
        return False
    if "enablePhaseModelRouting" in ws:
        return bool(ws.get("enablePhaseModelRouting"))
    return efficiency_high(ws)


def single_model_mode_active(ws: Optional[Dict[str, Any]] = None) -> bool:
    """True when every role should share one model to avoid reload thrash.

    "auto" only engages when we actually know the inference host's VRAM and it is too
    small to hold two models. Unknown capacity leaves the operator's config alone.
    """
    if ws is None:
        from backend.services.workflow_settings import get_workflow_settings

        ws = get_workflow_settings()
    mode = str(ws.get("singleModelMode") or "auto").strip().lower()
    if mode in ("on", "true", "yes", "1"):
        return True
    if mode in ("off", "false", "no", "0"):
        return False
    try:
        from backend.services.llm_capacity import resolve_inference_capacity

        capacity = resolve_inference_capacity(ws)
    except Exception:
        return False
    return bool(capacity.known and capacity.vram_mb is not None and capacity.vram_mb < 16000)


def effective_keep_alive(ws: Optional[Dict[str, Any]] = None) -> str:
    """How long the server should hold the model in VRAM after a turn.

    With one shared model there is nothing to evict, so holding it indefinitely removes
    reloads entirely. With several models on a card that only fits one, a long hold
    means the *next* role waits for an eviction, so we release promptly instead.
    """
    if ws is None:
        from backend.services.workflow_settings import get_workflow_settings

        ws = get_workflow_settings()
    configured = str(ws.get("ollamaKeepAlive") or "30m").strip()
    if single_model_mode_active(ws):
        return "-1"
    try:
        from backend.services.llm_capacity import resolve_inference_capacity

        capacity = resolve_inference_capacity(ws)
    except Exception:
        return configured
    if capacity.known and capacity.vram_mb is not None and capacity.vram_mb < 16000:
        return "5m"
    return configured


def single_model_name(ws: Optional[Dict[str, Any]] = None, *, fallback: str = "") -> str:
    """The one model every role uses in single-model mode (the Developer primary)."""
    from backend import state

    try:
        dev = str((getattr(state, "PRIMARY_MODELS", None) or {}).get("dev") or "").strip()
    except Exception:
        dev = ""
    return dev or str(fallback or "").strip()


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

    Explore prefers a fast tool-friendly coder; Patch/Verify prefer a reliable
    tool-calling coder (avoid general chat models like Gemma for patch).
    """
    if ws is None:
        from backend.services.workflow_settings import get_workflow_settings

        ws = get_workflow_settings()
    primary = (primary_model or "").strip() or "qwen2.5-coder:14b"

    if single_model_mode_active(ws):
        # One model for every role and phase: a lane change must never cost a reload.
        return single_model_name(ws, fallback=primary), "single_model"

    if role != "Developer" or not phase_model_routing_enabled(ws):
        return primary, "role_primary"

    fast = str(ws.get("discordModelPresetFast") or "").strip() or "qwen2.5-coder:7b"
    quality = str(ws.get("discordModelPresetQuality") or "").strip() or "qwen2.5-coder:14b"

    explore = str(ws.get("devExploreModel") or "").strip()
    if not explore:
        # Prefer explicit fast preset over backup (backup may be another heavy model).
        explore = fast
        if backup_model and _is_coder_model(str(backup_model)):
            # Only use backup for explore when it looks like a small/mid coder.
            explore = str(backup_model).strip() or explore
    if not explore:
        explore = "qwen2.5-coder:7b"
    # Never burn a heavy general-chat primary on Explore when a fast coder exists.
    if _is_heavy_general_model(explore) and fast:
        explore = fast

    patch = str(ws.get("devPatchModel") or "").strip()
    if not patch:
        if _is_heavy_general_model(primary) or not _is_coder_model(primary):
            patch = quality if _is_coder_model(quality) else "qwen2.5-coder:14b"
        else:
            patch = primary
    if not patch:
        patch = primary

    ph = str(phase or "explore").strip().lower()
    if ph in ("explore", ""):
        return explore, "phase_explore"
    # patch / verify / done / stuck → stronger coder (keep warm for verify)
    return patch, f"phase_{ph or 'patch'}"


def _is_coder_model(model: str) -> bool:
    m = (model or "").strip().lower()
    if not m:
        return False
    return any(tok in m for tok in ("coder", "codestral", "deepseek-coder", "starcoder", "qwen2.5-coder"))


def _is_heavy_general_model(model: str) -> bool:
    """True for large general/chat models that are slow and weak at native tool calls."""
    m = (model or "").strip().lower()
    if not m or _is_coder_model(m):
        return False
    return any(tok in m for tok in ("gemma", "llama3", "llama-3", "mistral", "phi4", "phi-4", "command-r"))


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
