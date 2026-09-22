"""In-step read_file cache — skip redundant reads when mtime unchanged."""

from __future__ import annotations

import hashlib
import os
from typing import Any, Dict, Optional, Tuple

from backend import state


def _safe_path(path: str) -> Optional[str]:
    try:
        from backend.workspace.files import resolve_workspace_path

        return resolve_workspace_path(path)
    except ValueError:
        return None


def _file_mtime(safe_path: str) -> Optional[float]:
    phys = os.path.join(state.WORKSPACE_DIR, safe_path)
    try:
        return os.path.getmtime(phys)
    except OSError:
        return None


def _content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8", errors="replace")).hexdigest()[:16]


class StepReadCache:
    """Per-step cache: path -> (mtime, content_hash, tool_output)."""

    def __init__(self) -> None:
        self._entries: Dict[str, Tuple[float, str, str]] = {}

    def get(self, path: str) -> Optional[str]:
        safe = _safe_path(path)
        if not safe or safe not in self._entries:
            return None
        mtime, _h, output = self._entries[safe]
        current_mtime = _file_mtime(safe)
        if current_mtime is None or current_mtime != mtime:
            self._entries.pop(safe, None)
            return None
        return output

    def put(self, path: str, tool_output: str) -> None:
        safe = _safe_path(path)
        if not safe or not tool_output or tool_output.startswith("Error:"):
            return
        mtime = _file_mtime(safe)
        if mtime is None:
            return
        self._entries[safe] = (mtime, _content_hash(tool_output), tool_output)

    def invalidate(self, path: str) -> None:
        safe = _safe_path(path)
        if safe:
            self._entries.pop(safe, None)


def get_step_read_cache(agent: Any) -> StepReadCache:
    cache = getattr(agent, "_step_read_cache", None)
    if cache is None:
        cache = StepReadCache()
        agent._step_read_cache = cache
    return cache
