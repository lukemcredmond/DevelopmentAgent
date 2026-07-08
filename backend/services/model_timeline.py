"""Merge LLM debug entries and tool execution events into a conversation timeline."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from backend import state


def _sort_key(timestamp: str) -> str:
    return (timestamp or "").strip() or "0000-00-00 00:00:00"


def build_model_timeline(
    *,
    task_id: Optional[str] = None,
    limit: int = 100,
) -> Dict[str, Any]:
    """Return merged timeline items (oldest first within result set)."""
    with state.STATE_LOCK:
        llm_logs = list(state.LLM_DEBUG_LOG)
        tool_logs = [dict(ev) for ev in state.TOOL_EXECUTION_LOG]

    if task_id:
        llm_logs = [e for e in llm_logs if e.get("taskId") == task_id]
        tool_logs = [e for e in tool_logs if e.get("taskId") == task_id]

    items: List[Dict[str, Any]] = []

    for entry in llm_logs:
        items.append(
            {
                "kind": "llm",
                "id": entry.get("id"),
                "timestamp": entry.get("timestamp"),
                "agent": entry.get("agent"),
                "agentId": entry.get("agentId"),
                "taskId": entry.get("taskId"),
                "runId": entry.get("runId"),
                "model": entry.get("model"),
                "iteration": entry.get("iteration"),
                "durationMs": entry.get("durationMs"),
                "error": entry.get("error"),
                "content": entry.get("responseContent") or "",
                "toolCalls": entry.get("responseToolCalls") or [],
                "toolNames": entry.get("toolNames") or [],
                "memoriesUsed": entry.get("memoriesUsed") or [],
                "decisionsIncluded": entry.get("decisionsIncluded"),
            }
        )

    seen_tool_ids: set[str] = set()
    for ev in tool_logs:
        event_id = str(ev.get("eventId") or "")
        if event_id and event_id in seen_tool_ids:
            continue
        if event_id:
            seen_tool_ids.add(event_id)
        status = str(ev.get("status") or ("completed" if ev.get("toolSuccess") is not False else "failed"))
        items.append(
            {
                "kind": "tool",
                "id": event_id or f"tool-{ev.get('toolName')}-{ev.get('timestamp')}",
                "timestamp": ev.get("timestamp"),
                "agent": ev.get("agent"),
                "taskId": ev.get("taskId"),
                "runId": ev.get("runId"),
                "toolName": ev.get("toolName"),
                "toolArgs": ev.get("toolArgs") or {},
                "toolOutput": ev.get("toolOutput") or "",
                "success": ev.get("toolSuccess") is not False,
                "status": status,
                "durationMs": ev.get("durationMs"),
                "source": ev.get("source"),
            }
        )

    items.sort(key=lambda x: _sort_key(str(x.get("timestamp") or "")))
    trimmed = items[-limit:] if len(items) > limit else items

    threads: Dict[str, List[Dict[str, Any]]] = {}
    for item in trimmed:
        tid = str(item.get("taskId") or "no-task")
        threads.setdefault(tid, []).append(item)

    thread_list = [
        {"taskId": tid, "items": thread_items}
        for tid, thread_items in sorted(threads.items(), key=lambda kv: _sort_key(kv[1][-1].get("timestamp", "") if kv[1] else ""), reverse=True)
    ]

    return {
        "items": trimmed,
        "threads": thread_list,
        "count": len(trimmed),
    }
