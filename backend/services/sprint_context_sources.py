"""Snapshot of sprint prompt context inject (Qdrant, files, packer) for UI."""

from __future__ import annotations

from typing import Any, Dict, Optional

from backend import state


def set_last_sprint_context_sources(payload: Dict[str, Any]) -> None:
    state.LAST_SPRINT_CONTEXT_SOURCES = dict(payload)


def get_last_sprint_context_sources() -> Optional[Dict[str, Any]]:
    raw = getattr(state, "LAST_SPRINT_CONTEXT_SOURCES", None)
    return dict(raw) if isinstance(raw, dict) else None


def build_context_sources_snapshot(
    *,
    task_id: str,
    agent_role: str,
    local_slm: bool,
    semantic_paths: list,
    file_paths: list,
    graph_used: bool,
    pack_mode: str,
    codebase_pack_chars: int,
) -> Dict[str, Any]:
    qdrant_chunks_total = 0
    try:
        from backend.storage.code_index import CodeIndexEngine

        qdrant_chunks_total = int(CodeIndexEngine().index_status().get("chunks") or 0)
    except Exception:
        qdrant_chunks_total = 0

    return {
        "taskId": task_id,
        "agentRole": agent_role,
        "localSlmProfile": local_slm,
        "semanticUsed": bool(semantic_paths),
        "semanticChunkCount": len(semantic_paths),
        "filePreloadCount": len(file_paths),
        "graphUsed": graph_used,
        "contextPacker": (pack_mode or "off").strip().lower(),
        "contextPackerChars": int(codebase_pack_chars or 0),
        "qdrantIndexChunks": qdrant_chunks_total,
    }
