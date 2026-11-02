"""Coalesce SQLite/settings writes off the agent step hot path."""

from __future__ import annotations

import logging
import threading
import time
from typing import Set

logger = logging.getLogger(__name__)

_FLUSH_INTERVAL_SEC = 0.35
_lock = threading.Lock()
_dirty: Set[str] = set()
_started = False
_thread: threading.Thread | None = None


def _ensure_worker() -> None:
    global _started, _thread
    with _lock:
        if _started:
            return
        _started = True
        _thread = threading.Thread(target=_worker_loop, name="persistence-flush", daemon=True)
        _thread.start()


def _worker_loop() -> None:
    while True:
        time.sleep(_FLUSH_INTERVAL_SEC)
        flush_persistence(force=False)


def mark_dirty(kind: str) -> None:
    _ensure_worker()
    with _lock:
        _dirty.add(kind)


def flush_persistence(*, force: bool = True) -> None:
    from backend import state

    with _lock:
        if not _dirty and not force:
            return
        kinds = set(_dirty)
        _dirty.clear()

    if not kinds and not force:
        return

    if "project_logs" in kinds or force:
        try:
            state.storage.save_project_logs(state.CURRENT_PROJECT_ID, state.SYSTEM_LOGS)
        except Exception:
            logger.exception("Failed to flush project logs")

    if "llm_debug_log" in kinds or force:
        try:
            from backend.services.llm_debug_log import persist_llm_log_sync

            persist_llm_log_sync()
        except Exception:
            logger.exception("Failed to flush LLM debug log")

    if "step_checkpoint" in kinds or force:
        try:
            from backend.services.step_diagnostics import flush_step_checkpoint_sync

            flush_step_checkpoint_sync()
        except Exception:
            logger.exception("Failed to flush step checkpoint")

    if "project_state" in kinds or force:
        try:
            from backend.services.project_service import save_current_project_state_sync

            save_current_project_state_sync()
        except Exception:
            logger.exception("Failed to flush project state")
