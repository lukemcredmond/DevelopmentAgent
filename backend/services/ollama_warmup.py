"""Best-effort model warm / unload helpers (never block long)."""

from __future__ import annotations

import threading
import time
from typing import Optional

from backend.services.logs import add_system_log
from backend.services.workflow_settings import get_workflow_settings

_logged_compat_noop = False


def _chat_provider(*, timeout_sec: float = 2.0):
    from backend.services.llm_provider import get_chat_provider

    provider = get_chat_provider()
    provider.timeout_sec = timeout_sec
    return provider


def _log_compat_noop_once(action: str) -> None:
    global _logged_compat_noop
    if _logged_compat_noop:
        return
    _logged_compat_noop = True
    add_system_log(
        "System",
        "info",
        f"{action} skipped — current LLM provider does not support keep_alive / VRAM unload",
    )


def unload_model(model: str, *, timeout_sec: float = 2.0) -> bool:
    """Best-effort unload via keep_alive=0. Returns True on apparent success."""
    model = (model or "").strip()
    if not model:
        return False
    try:
        provider = _chat_provider(timeout_sec=timeout_sec)
        if not provider.capabilities.vram_unload:
            _log_compat_noop_once("VRAM unload")
            return False
        return provider.unload(model)
    except Exception as exc:
        add_system_log(
            "System",
            "info",
            f"preload skipped: unload {model} failed ({type(exc).__name__})",
        )
        return False


def warm_model(model: str, *, timeout_sec: float = 2.0) -> bool:
    """Cheap generate/chat to pull model into memory. Best-effort."""
    model = (model or "").strip()
    if not model:
        return False
    try:
        provider = _chat_provider(timeout_sec=timeout_sec)
        if not provider.capabilities.keep_alive:
            _log_compat_noop_once("Model warmup")
            return False
        ws = get_workflow_settings()
        keep_alive = ws.get("ollamaKeepAlive") or "30m"
        return provider.warm(model, keep_alive=str(keep_alive))
    except Exception as exc:
        add_system_log(
            "System",
            "info",
            f"preload skipped: warm {model} failed ({type(exc).__name__})",
        )
        return False


def maybe_vram_unload_primary(primary: str, *, backup: str = "") -> None:
    """
    If VRAM usage > 85% and enableVramAwareModelSwap, unload primary before backup load.
    No-op when nvidia-smi is unavailable.
    """
    ws = get_workflow_settings()
    if not ws.get("enableVramAwareModelSwap", True):
        return
    primary = (primary or "").strip()
    if not primary:
        return
    try:
        from backend.services.system_capacity import probe_system_capacity

        cap = probe_system_capacity()
    except Exception:
        return
    vram = cap.get("vramMb")
    used = cap.get("vramUsedMb")
    if not isinstance(vram, int) or vram <= 0 or not isinstance(used, (int, float)):
        return
    if float(used) / float(vram) <= 0.85:
        return
    add_system_log(
        "System",
        "info",
        f"VRAM-aware swap: unloading primary {primary} "
        f"(used {int(used)}/{vram} MB) before backup{f' {backup}' if backup else ''}",
    )
    unload_model(primary, timeout_sec=2.0)
    time.sleep(0.4)


def preload_backup_model_async(backup: str, *, primary: str = "") -> None:
    """Fire-and-forget warm of backup model; never blocks the caller more than ~0s."""
    backup = (backup or "").strip()
    if not backup:
        add_system_log("System", "info", "preload skipped: no backup model name")
        return

    def _run() -> None:
        try:
            add_system_log("System", "info", f"Preloading backup model {backup}…")
            maybe_vram_unload_primary(primary, backup=backup)
            ok = warm_model(backup, timeout_sec=2.0)
            if ok:
                add_system_log("System", "info", f"Preloaded backup model {backup}")
            else:
                add_system_log("System", "info", f"preload skipped: warm of {backup} did not succeed")
        except Exception as exc:
            add_system_log(
                "System",
                "info",
                f"preload skipped: {type(exc).__name__}",
            )

    threading.Thread(target=_run, name=f"ollama-warmup-{backup[:24]}", daemon=True).start()
