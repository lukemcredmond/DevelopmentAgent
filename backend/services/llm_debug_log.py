"""Persist Ollama request/response payloads for debugging."""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence

from backend import state

MAX_LLM_LOG_ENTRIES = 500
MAX_MESSAGE_CHARS = 32000


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
    error_type: Optional[str] = None,
    run_id: Optional[str] = None,
    memories_used: Optional[List[Dict[str, Any]]] = None,
    decisions_included: Optional[int] = None,
    prompt_tokens: int = 0,
    eval_tokens: int = 0,
    total_tokens: int = 0,
    tokens_reported: bool = False,
    prompt_unchanged_inject: bool = False,
    prompt_section: Optional[str] = None,
    decision_trace: Optional[Dict[str, Any]] = None,
    echo_detected: bool = False,
    status: str = "completed",
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
        "promptTokens": int(prompt_tokens or 0),
        "evalTokens": int(eval_tokens or 0),
        "totalTokens": int(total_tokens or (prompt_tokens or 0) + (eval_tokens or 0)),
        "tokensReported": bool(tokens_reported),
        "status": status,
    }
    if error:
        entry["error"] = error
    if error_type:
        entry["errorType"] = error_type
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
    if prompt_unchanged_inject:
        entry["promptUnchangedInject"] = True
    if prompt_section:
        entry["promptSection"] = prompt_section
    if decision_trace:
        entry["decisionTrace"] = decision_trace
    if echo_detected:
        entry["echoDetected"] = True
    with state.STATE_LOCK:
        state.LLM_DEBUG_LOG.append(entry)
        overflow = len(state.LLM_DEBUG_LOG) - MAX_LLM_LOG_ENTRIES
        if overflow > 0:
            del state.LLM_DEBUG_LOG[:overflow]
        persist_llm_log()
    return entry


def complete_llm_log_entry(
    entry_id: str,
    *,
    response_content: Optional[str] = None,
    response_tool_calls: Optional[List[Any]] = None,
    duration_ms: int = 0,
    error: Optional[str] = None,
    error_type: Optional[str] = None,
    prompt_tokens: int = 0,
    eval_tokens: int = 0,
    total_tokens: int = 0,
    tokens_reported: bool = False,
) -> Optional[Dict[str, Any]]:
    """Complete an existing running request without adding a duplicate log row."""
    with state.STATE_LOCK:
        for entry in reversed(state.LLM_DEBUG_LOG):
            if entry.get("id") != entry_id:
                continue
            entry["status"] = "failed" if error else "completed"
            entry["responseContent"] = (response_content or "")[:MAX_MESSAGE_CHARS]
            entry["responseToolCalls"] = response_tool_calls or []
            entry["durationMs"] = int(duration_ms or 0)
            entry["promptTokens"] = int(prompt_tokens or 0)
            entry["evalTokens"] = int(eval_tokens or 0)
            entry["totalTokens"] = int(total_tokens or (prompt_tokens or 0) + (eval_tokens or 0))
            entry["tokensReported"] = bool(tokens_reported)
            if error:
                entry["error"] = error
            else:
                entry.pop("error", None)
            if error_type:
                entry["errorType"] = error_type
            else:
                entry.pop("errorType", None)
            persist_llm_log()
            return dict(entry)
    return None


def amend_llm_log_entry(
    task_id: Optional[str],
    iteration: int,
    *,
    decision_trace: Optional[Dict[str, Any]] = None,
    echo_detected: Optional[bool] = None,
) -> None:
    """Patch the most recent log row for this task+iteration (post echo/trace analysis)."""
    with state.STATE_LOCK:
        for entry in reversed(state.LLM_DEBUG_LOG):
            if entry.get("taskId") != task_id:
                continue
            if int(entry.get("iteration") or 0) != int(iteration):
                continue
            if decision_trace is not None:
                entry["decisionTrace"] = decision_trace
            if echo_detected is not None:
                entry["echoDetected"] = bool(echo_detected)
            persist_llm_log()
            return


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
