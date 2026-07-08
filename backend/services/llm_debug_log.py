"""Persist Ollama request/response payloads for debugging."""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence

from backend import state

MAX_LLM_LOG_ENTRIES = 500
MAX_MESSAGE_CHARS = 8000


def _log_key(project_id: str) -> str:
    return f"llm_log:{project_id}"


def _truncate_messages(messages: Sequence[Any]) -> List[Any]:
    out: List[Any] = []
    for msg in messages:
        if isinstance(msg, dict):
            copy = dict(msg)
            content = copy.get("content")
            if isinstance(content, str) and len(content) > MAX_MESSAGE_CHARS:
                copy["content"] = content[:MAX_MESSAGE_CHARS] + "…[truncated]"
            out.append(copy)
        else:
            content = getattr(msg, "content", None)
            if isinstance(content, str) and len(content) > MAX_MESSAGE_CHARS:
                out.append({"role": getattr(msg, "role", "?"), "content": content[:MAX_MESSAGE_CHARS] + "…"})
            else:
                out.append(str(msg)[:MAX_MESSAGE_CHARS])
    return out


def persist_llm_log() -> None:
    state.storage.set_setting(
        _log_key(state.CURRENT_PROJECT_ID),
        json.dumps(state.LLM_DEBUG_LOG[-MAX_LLM_LOG_ENTRIES:]),
    )


def load_llm_log_for_project(project_id: str) -> None:
    raw = state.storage.get_setting(_log_key(project_id))
    state.LLM_DEBUG_LOG.clear()
    if not raw:
        return
    try:
        loaded = json.loads(raw)
        if isinstance(loaded, list):
            state.LLM_DEBUG_LOG.extend(loaded[-MAX_LLM_LOG_ENTRIES:])
    except json.JSONDecodeError:
        pass


def append_llm_log_entry(
    *,
    agent: str,
    agent_id: str,
    task_id: Optional[str],
    model: str,
    iteration: int,
    request_messages: Sequence[Any],
    tool_names: Optional[List[str]] = None,
    response_content: Optional[str] = None,
    response_tool_calls: Optional[List[Any]] = None,
    duration_ms: int = 0,
    error: Optional[str] = None,
    run_id: Optional[str] = None,
    memories_used: Optional[List[Dict[str, Any]]] = None,
    decisions_included: Optional[int] = None,
) -> Dict[str, Any]:
    entry: Dict[str, Any] = {
        "id": uuid.uuid4().hex[:12],
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "agent": agent,
        "agentId": agent_id,
        "taskId": task_id,
        "runId": run_id,
        "model": model,
        "iteration": iteration,
        "requestMessages": _truncate_messages(request_messages),
        "toolNames": tool_names or [],
        "responseContent": (response_content or "")[:MAX_MESSAGE_CHARS],
        "responseToolCalls": response_tool_calls or [],
        "durationMs": duration_ms,
        "error": error,
    }
    if memories_used is not None:
        entry["memoriesUsed"] = [
            {
                "category": str(m.get("category") or ""),
                "content": str(m.get("content") or "")[:300],
            }
            for m in memories_used
        ]
    if decisions_included is not None:
        entry["decisionsIncluded"] = decisions_included
    with state.STATE_LOCK:
        state.LLM_DEBUG_LOG.append(entry)
        overflow = len(state.LLM_DEBUG_LOG) - MAX_LLM_LOG_ENTRIES
        if overflow > 0:
            del state.LLM_DEBUG_LOG[:overflow]
        persist_llm_log()
    return entry


def get_llm_logs(
    limit: int = 200,
    agent: Optional[str] = None,
    task_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    with state.STATE_LOCK:
        logs = list(reversed(state.LLM_DEBUG_LOG))
    if agent:
        logs = [e for e in logs if e.get("agent") == agent or e.get("agentId") == agent]
    if task_id:
        logs = [e for e in logs if e.get("taskId") == task_id]
    return logs[:limit]


def clear_llm_log() -> Dict[str, Any]:
    with state.STATE_LOCK:
        state.LLM_DEBUG_LOG.clear()
        persist_llm_log()
    return {"ok": True, "entries": []}
