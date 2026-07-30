"""Build on-demand task flow (LLM + tools) from persisted logs and diagnostics files."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from backend import state
from backend.config import diagnostics_dir

FULL_OUTPUT_CAP = 32000


def _sort_key(timestamp: str) -> str:
    return (timestamp or "").strip() or "0000-00-00 00:00:00"


def _safe_task_slug(task_id: str) -> str:
    import re

    return re.sub(r"[^\w\-]", "_", task_id)[:40]


def _cap_text(text: Any, *, include_full: bool, default_cap: int = 8000) -> str:
    s = text if isinstance(text, str) else ("" if text is None else str(text))
    cap = FULL_OUTPUT_CAP if include_full else default_cap
    if len(s) > cap:
        return s[: cap - 20] + "\n…[truncated]"
    return s


def _cap_messages(messages: Any, *, include_full: bool) -> List[Any]:
    if not isinstance(messages, list):
        return []
    out: List[Any] = []
    for msg in messages:
        if isinstance(msg, dict):
            copy = dict(msg)
            if "content" in copy:
                copy["content"] = _cap_text(copy.get("content"), include_full=include_full)
            out.append(copy)
        else:
            out.append(_cap_text(msg, include_full=include_full, default_cap=2000))
    return out


def list_step_traces_for_task(task_id: str) -> List[Dict[str, Any]]:
    """List diagnostics JSON files for a task (newest first)."""
    slug = _safe_task_slug(task_id)
    project_dir = diagnostics_dir(state.CURRENT_PROJECT_ID)
    if not project_dir.is_dir():
        return []
    files = sorted(
        project_dir.glob(f"step-{slug}-*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    out: List[Dict[str, Any]] = []
    for path in files[:20]:
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                continue
            if str(data.get("taskId") or "") not in ("", task_id) and task_id not in path.name:
                continue
            out.append(
                {
                    "path": str(path),
                    "traceId": data.get("traceId"),
                    "startedAt": data.get("startedAt"),
                    "endedAt": data.get("endedAt"),
                    "status": data.get("status"),
                    "exitReason": data.get("exitReason"),
                    "agent": data.get("agent"),
                }
            )
        except (OSError, json.JSONDecodeError):
            continue
    return out


def load_step_trace(path: str) -> Optional[Dict[str, Any]]:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def _ensure_logs_loaded() -> None:
    """Reload from SQLite settings if in-memory rings are empty."""
    if not state.LLM_DEBUG_LOG:
        try:
            from backend.services.llm_debug_log import load_llm_log_for_project

            load_llm_log_for_project(state.CURRENT_PROJECT_ID)
        except Exception:
            pass
    if not state.TOOL_EXECUTION_LOG:
        try:
            from backend.services.tool_execution_service import load_tool_log_for_project

            load_tool_log_for_project(state.CURRENT_PROJECT_ID)
        except Exception:
            pass


def build_task_flow(
    task_id: str,
    *,
    limit: int = 80,
    include_full: bool = True,
) -> Dict[str, Any]:
    """Ordered LLM↔tool nodes for Task Detail Flow tab (not board memory)."""
    _ensure_logs_loaded()
    tid = str(task_id)

    with state.STATE_LOCK:
        llm_logs = [e for e in list(state.LLM_DEBUG_LOG) if e.get("taskId") == tid]
        tool_logs = [dict(ev) for ev in state.TOOL_EXECUTION_LOG if ev.get("taskId") == tid]

    nodes: List[Dict[str, Any]] = []

    for entry in llm_logs:
        nodes.append(
            {
                "kind": "llm",
                "id": entry.get("id") or f"llm-{entry.get('timestamp')}",
                "timestamp": entry.get("timestamp"),
                "agent": entry.get("agent"),
                "agentId": entry.get("agentId"),
                "taskId": tid,
                "runId": entry.get("runId"),
                "model": entry.get("model"),
                "iteration": entry.get("iteration"),
                "durationMs": entry.get("durationMs"),
                "error": entry.get("error"),
                "requestMessages": _cap_messages(entry.get("requestMessages"), include_full=include_full),
                "responseContent": _cap_text(entry.get("responseContent"), include_full=include_full),
                "toolCalls": entry.get("responseToolCalls") or [],
                "toolNames": entry.get("toolNames") or [],
                "memoriesUsed": entry.get("memoriesUsed") or [],
                "decisionsIncluded": entry.get("decisionsIncluded"),
                "source": "llm_log",
            }
        )

    seen_tool: set[str] = set()
    for ev in tool_logs:
        event_id = str(ev.get("eventId") or "")
        if event_id and event_id in seen_tool:
            continue
        if event_id:
            seen_tool.add(event_id)
        nodes.append(
            {
                "kind": "tool",
                "id": event_id or f"tool-{ev.get('toolName')}-{ev.get('timestamp')}",
                "timestamp": ev.get("timestamp"),
                "agent": ev.get("agent"),
                "taskId": tid,
                "runId": ev.get("runId"),
                "toolName": ev.get("toolName"),
                "toolArgs": ev.get("toolArgs") or {},
                "toolOutput": _cap_text(ev.get("toolOutput") or "", include_full=include_full),
                "success": ev.get("toolSuccess") is not False,
                "status": str(ev.get("status") or ("completed" if ev.get("toolSuccess") is not False else "failed")),
                "durationMs": ev.get("durationMs"),
                "source": "tool_log",
            }
        )

    # Enrich from diagnostics files (summaries / events when logs pruned)
    traces_meta = list_step_traces_for_task(tid)
    for meta in traces_meta[:5]:
        path = str(meta.get("path") or "")
        data = load_step_trace(path) if path else None
        if not data:
            continue
        for call in data.get("ollamaCalls") or []:
            if not isinstance(call, dict):
                continue
            # Diagnostics usually lack full messages — add summary nodes only if no llm_log match
            ts = call.get("timestamp") or data.get("startedAt")
            nodes.append(
                {
                    "kind": "llm",
                    "id": f"diag-llm-{data.get('traceId')}-{call.get('iteration')}",
                    "timestamp": ts,
                    "agent": data.get("agent"),
                    "taskId": tid,
                    "iteration": call.get("iteration"),
                    "durationMs": call.get("durationMs"),
                    "error": call.get("error"),
                    "requestMessages": [],
                    "responseContent": "",
                    "toolCalls": [{"name": n} for n in (call.get("toolCalls") or [])],
                    "toolNames": call.get("toolCalls") or [],
                    "source": "diagnostics",
                    "traceId": data.get("traceId"),
                    "textChars": call.get("textChars"),
                }
            )
        for tool in data.get("toolsLog") or []:
            if not isinstance(tool, dict):
                continue
            nodes.append(
                {
                    "kind": "tool",
                    "id": f"diag-tool-{data.get('traceId')}-{tool.get('toolName')}-{tool.get('timestamp')}",
                    "timestamp": tool.get("timestamp") or data.get("startedAt"),
                    "agent": data.get("agent"),
                    "taskId": tid,
                    "toolName": tool.get("toolName"),
                    "toolArgs": {},
                    "toolOutput": _cap_text(tool.get("summary") or "", include_full=include_full, default_cap=300),
                    "success": tool.get("success") is not False,
                    "status": "completed" if tool.get("success") is not False else "failed",
                    "durationMs": tool.get("durationMs"),
                    "source": "diagnostics",
                    "traceId": data.get("traceId"),
                }
            )

    # Prefer llm_log/tool_log over diagnostics duplicates (same iteration+tool+timestamp window)
    # Drop pure diagnostics llm nodes when a richer llm_log node exists for same iteration/time.
    rich_iters = {
        (n.get("iteration"), str(n.get("timestamp") or "")[:16])
        for n in nodes
        if n.get("kind") == "llm" and n.get("source") == "llm_log"
    }
    filtered: List[Dict[str, Any]] = []
    for n in nodes:
        if n.get("kind") == "llm" and n.get("source") == "diagnostics":
            key = (n.get("iteration"), str(n.get("timestamp") or "")[:16])
            if key in rich_iters or (n.get("iteration"),) in {(i,) for i, _ in rich_iters}:
                # Skip thin diagnostics when we have full llm_log for this task
                if any(
                    x.get("kind") == "llm" and x.get("source") == "llm_log" and x.get("iteration") == n.get("iteration")
                    for x in nodes
                ):
                    continue
        if n.get("kind") == "tool" and n.get("source") == "diagnostics":
            # Skip if tool_log has same toolName near same timestamp
            tname = n.get("toolName")
            if any(
                x.get("kind") == "tool"
                and x.get("source") == "tool_log"
                and x.get("toolName") == tname
                for x in nodes
            ):
                continue
        filtered.append(n)

    filtered.sort(key=lambda x: _sort_key(str(x.get("timestamp") or "")))
    trimmed = filtered[-limit:] if len(filtered) > limit else filtered

    return {
        "taskId": tid,
        "nodes": trimmed,
        "traces": traces_meta,
        "count": len(trimmed),
        "includeFull": include_full,
    }
