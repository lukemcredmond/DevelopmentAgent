"""Per-agent time and token usage rolled up onto board tasks."""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple


def extract_ollama_token_counts(response: Any) -> Tuple[int, int, int, bool]:
    """Return (prompt_tokens, eval_tokens, total_tokens, tokens_reported).

    Reads Ollama ChatResponse fields (attrs or dict). Missing → zeros + reported=False.
    """
    if response is None:
        return 0, 0, 0, False

    def _get(key: str) -> Optional[int]:
        raw = None
        if isinstance(response, dict):
            raw = response.get(key)
        else:
            raw = getattr(response, key, None)
            if raw is None and hasattr(response, "model_dump"):
                try:
                    dumped = response.model_dump()
                    if isinstance(dumped, dict):
                        raw = dumped.get(key)
                except Exception:
                    pass
        if raw is None:
            return None
        try:
            return int(raw)
        except (TypeError, ValueError):
            return None

    prompt = _get("prompt_eval_count")
    eval_ = _get("eval_count")
    if prompt is None and eval_ is None:
        return 0, 0, 0, False
    p = prompt or 0
    e = eval_ or 0
    return p, e, p + e, True


def empty_usage_entry() -> Dict[str, Any]:
    return {
        "stepCount": 0,
        "callCount": 0,
        "durationMs": 0,
        "ollamaMs": 0,
        "toolMs": 0,
        "promptTokens": 0,
        "evalTokens": 0,
        "totalTokens": 0,
        "tokensReported": False,
    }


def record_agent_usage(
    task_id: Optional[str],
    role: str,
    *,
    duration_ms: int = 0,
    ollama_ms: int = 0,
    tool_ms: int = 0,
    prompt_tokens: int = 0,
    eval_tokens: int = 0,
    call_count: int = 0,
    step_count: int = 0,
    tokens_reported: bool = False,
) -> Optional[Dict[str, Any]]:
    """Merge usage into task['agentUsage'][role]. Returns updated entry or None."""
    if not task_id or not role:
        return None
    from backend.agents.task_context import find_task_by_id, normalize_task

    task = find_task_by_id(str(task_id))
    if not task:
        return None
    normalize_task(task)
    usage = task.get("agentUsage")
    if not isinstance(usage, dict):
        usage = {}
        task["agentUsage"] = usage
    entry = usage.get(role)
    if not isinstance(entry, dict):
        entry = empty_usage_entry()
        usage[role] = entry

    entry["durationMs"] = int(entry.get("durationMs") or 0) + max(0, int(duration_ms or 0))
    entry["ollamaMs"] = int(entry.get("ollamaMs") or 0) + max(0, int(ollama_ms or 0))
    entry["toolMs"] = int(entry.get("toolMs") or 0) + max(0, int(tool_ms or 0))
    entry["promptTokens"] = int(entry.get("promptTokens") or 0) + max(0, int(prompt_tokens or 0))
    entry["evalTokens"] = int(entry.get("evalTokens") or 0) + max(0, int(eval_tokens or 0))
    entry["totalTokens"] = int(entry.get("promptTokens") or 0) + int(entry.get("evalTokens") or 0)
    entry["callCount"] = int(entry.get("callCount") or 0) + max(0, int(call_count or 0))
    entry["stepCount"] = int(entry.get("stepCount") or 0) + max(0, int(step_count or 0))
    if tokens_reported:
        entry["tokensReported"] = True

    # Persist visibility on step boundaries (avoid SSE spam per Ollama call)
    if step_count > 0:
        try:
            from backend.services.board_service import publish_board_delta

            publish_board_delta(str(task_id), source="agent_usage")
        except Exception:
            pass
    return entry


def record_ollama_call_usage(
    *,
    task_id: Optional[str],
    role: str,
    duration_ms: int,
    prompt_tokens: int = 0,
    eval_tokens: int = 0,
    tokens_reported: bool = False,
) -> None:
    """Live bump after each Ollama call (mid-step visibility)."""
    record_agent_usage(
        task_id,
        role,
        ollama_ms=duration_ms,
        prompt_tokens=prompt_tokens,
        eval_tokens=eval_tokens,
        call_count=1,
        tokens_reported=tokens_reported,
    )


def record_step_usage_from_trace(trace: Any) -> None:
    """At step finalize: add wall + tool ms and one stepCount (Ollama already live-bumped)."""
    if trace is None:
        return
    task_id = getattr(trace, "task_id", None) or (trace.get("taskId") if isinstance(trace, dict) else None)
    role = getattr(trace, "agent", None) or (trace.get("agent") if isinstance(trace, dict) else None)
    if not task_id or not role:
        return

    from datetime import datetime

    if hasattr(trace, "started_monotonic"):
        duration_ms = int((datetime.now() - trace.started_monotonic).total_seconds() * 1000)
        tools_log = getattr(trace, "tools_log", None) or []
    elif isinstance(trace, dict):
        duration_ms = int(trace.get("durationMs") or 0)
        tools_log = trace.get("toolsLog") or []
    else:
        duration_ms = 0
        tools_log = []

    tool_ms = 0
    for entry in tools_log:
        if isinstance(entry, dict) and isinstance(entry.get("durationMs"), (int, float)):
            tool_ms += int(entry["durationMs"])

    # Ollama ms/tokens already recorded live per call — only add wall + tools + step
    record_agent_usage(
        str(task_id),
        str(role),
        duration_ms=duration_ms,
        tool_ms=tool_ms,
        step_count=1,
    )
