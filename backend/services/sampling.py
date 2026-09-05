"""Per-role sampling parameters for agent LLM turns.

Near-greedy decoding (the previous hard-coded temperature=0.1 with no repetition
penalty) is a well-known cause of degenerate repetition in small local models: the
model re-emits the same tool call or echoes prior tool output. The pipeline had grown
several guards for that symptom (duplicate tool policy, echo stop). Setting a mild
repeat penalty prevents it at the sampler instead.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from backend.services.prompt_budget import normalize_role_key

# Deterministic enough for code edits, with enough headroom to escape a repeat loop.
DEFAULT_SAMPLING: Dict[str, float] = {
    "temperature": 0.2,
    "top_p": 0.9,
    "repeat_penalty": 1.05,
}

# Planning benefits from a little more diversity; editing wants determinism.
ROLE_SAMPLING_DEFAULTS: Dict[str, Dict[str, float]] = {
    "po": {"temperature": 0.4, "top_p": 0.95, "repeat_penalty": 1.05, "num_predict": 1024},
    "dev": {"temperature": 0.15, "top_p": 0.9, "repeat_penalty": 1.08},
    "cr": {"temperature": 0.2, "top_p": 0.9, "repeat_penalty": 1.05},
    "qa": {"temperature": 0.2, "top_p": 0.9, "repeat_penalty": 1.05},
}

_ALLOWED_KEYS = ("temperature", "top_p", "repeat_penalty", "top_k", "min_p", "num_predict")


def _coerce_number(value: Any) -> Optional[float]:
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    return num


def sampling_options_for_role(
    role: Optional[str],
    *,
    ws: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Resolve sampling for a role: role override -> global override -> defaults."""
    if ws is None:
        from backend.services.workflow_settings import get_workflow_settings

        ws = get_workflow_settings()

    key = normalize_role_key(role)
    resolved: Dict[str, Any] = dict(DEFAULT_SAMPLING)
    if key and key in ROLE_SAMPLING_DEFAULTS:
        resolved.update(ROLE_SAMPLING_DEFAULTS[key])

    global_override = ws.get("samplingDefaults")
    if isinstance(global_override, dict):
        for name in _ALLOWED_KEYS:
            if name in global_override:
                num = _coerce_number(global_override[name])
                if num is not None:
                    resolved[name] = num

    by_role = ws.get("samplingByRole")
    if isinstance(by_role, dict) and key:
        role_override = by_role.get(key)
        if isinstance(role_override, dict):
            for name in _ALLOWED_KEYS:
                if name in role_override:
                    num = _coerce_number(role_override[name])
                    if num is not None:
                        resolved[name] = num

    # top_k must be an int for Ollama; drop nonsense values rather than erroring.
    if "top_k" in resolved:
        try:
            resolved["top_k"] = int(resolved["top_k"])
        except (TypeError, ValueError):
            resolved.pop("top_k", None)
    if "num_predict" in resolved:
        try:
            resolved["num_predict"] = int(resolved["num_predict"])
        except (TypeError, ValueError):
            resolved.pop("num_predict", None)

    return resolved
