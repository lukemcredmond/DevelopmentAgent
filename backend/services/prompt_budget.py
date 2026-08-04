"""Prompt size helpers scaled to Ollama num_ctx."""

from __future__ import annotations

from typing import Any, Dict, Optional

DEFAULT_NUM_CTX = 32768

_ROLE_KEYS = frozenset({"po", "dev", "cr", "qa"})

_ROLE_ALIASES = {
    "product owner": "po",
    "developer": "dev",
    "code reviewer": "cr",
    "qa tester": "qa",
    "po": "po",
    "dev": "dev",
    "cr": "cr",
    "qa": "qa",
}


def normalize_role_key(role: Optional[str]) -> Optional[str]:
    if not role:
        return None
    key = str(role).strip().lower()
    return _ROLE_ALIASES.get(key, key if key in _ROLE_KEYS else None)


def resolve_ollama_num_ctx(
    role: Optional[str] = None,
    *,
    settings: Optional[Dict[str, Any]] = None,
) -> int:
    """
    Resolve num_ctx for a role.
    Dev defaults to global; PO/CR/QA default to min(global, 16384) when unset in map.
    When ollamaNumCtxAuto and VRAM tier is low/minimal, halve Dev ctx.
    """
    try:
        from backend.services.workflow_settings import get_workflow_settings

        ws = settings if settings is not None else get_workflow_settings()
    except Exception:
        ws = settings or {}

    global_ctx = int(ws.get("ollamaNumCtx") or DEFAULT_NUM_CTX)
    global_ctx = max(1024, global_ctx)
    by_role = ws.get("ollamaNumCtxByRole") or {}
    if not isinstance(by_role, dict):
        by_role = {}

    key = normalize_role_key(role)
    if key and key in by_role and by_role[key] not in (None, "", 0, "0"):
        try:
            ctx = max(1024, int(by_role[key]))
        except (TypeError, ValueError):
            ctx = global_ctx if key == "dev" else min(global_ctx, 16384)
    elif key == "dev" or key is None:
        ctx = global_ctx
    else:
        ctx = min(global_ctx, 16384)

    if key == "dev" and ws.get("ollamaNumCtxAuto"):
        try:
            from backend.services.system_capacity import probe_system_capacity

            tier = str(probe_system_capacity().get("tier") or "")
            if tier in ("low", "minimal"):
                ctx = max(2048, ctx // 2)
        except Exception:
            pass
    return ctx


def initial_ollama_num_ctx(
    role: Optional[str] = None,
    *,
    settings: Optional[Dict[str, Any]] = None,
) -> int:
    """num_ctx for a new agent step (adaptive low start, or full ceiling when adaptive is off)."""
    ceiling = resolve_ollama_num_ctx(role, settings=settings)
    try:
        from backend.services.workflow_settings import get_workflow_settings

        ws = settings if settings is not None else get_workflow_settings()
    except Exception:
        ws = settings or {}
    if not ws.get("ollamaNumCtxAdaptive"):
        return ceiling
    try:
        start = int(ws.get("ollamaNumCtxAdaptiveStart") or 8192)
    except (TypeError, ValueError):
        start = 8192
    return min(ceiling, max(2048, start))


def bump_ollama_num_ctx(current: int, ceiling: int, *, step: int = 8192) -> Optional[int]:
    """Next num_ctx after overflow, or None if already at ceiling."""
    if current >= ceiling:
        return None
    step = max(1024, int(step))
    doubled = min(ceiling, current * 2)
    stepped = min(ceiling, current + step)
    nxt = doubled if doubled > stepped else stepped
    if nxt <= current:
        return None
    return nxt


def sprint_file_context_max_chars(num_ctx: int) -> int:
    """Max chars for pre-loaded sprint file context (~60% of token budget as chars)."""
    return min(12000, max(2000, (num_ctx // 4) * 3))


def truncate_brief(brief: str, num_ctx: int, max_chars: int = 6000) -> str:
    """Truncate project brief to fit context budget."""
    from backend.services.prompt_profile import is_local_slm_profile

    if is_local_slm_profile():
        max_chars = min(max_chars, max(1500, num_ctx))
    budget = min(max_chars, num_ctx * 2)
    if len(brief) <= budget:
        return brief
    return brief[: budget - 40] + "\n...[brief truncated for context budget]\n"


def skills_context_max_chars(num_ctx: int) -> int:
    from backend.services.prompt_profile import is_local_slm_profile

    if is_local_slm_profile():
        return 0
    return min(8000, max(2000, num_ctx))


def workspace_file_list_cap(num_ctx: int) -> int:
    return 30 if num_ctx >= 8192 else 15


def semantic_sprint_context_max_chars(num_ctx: int) -> int:
    """Budget for semantic index chunks in sprint pre-load."""
    return min(6000, max(1500, sprint_file_context_max_chars(num_ctx) // 2))
