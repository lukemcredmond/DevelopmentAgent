"""Per-card LLM conversation continuity across sprint steps."""

from __future__ import annotations

import copy
import json
from typing import Any, Dict, List, MutableSequence, Optional, Sequence

from backend.agents.task_context import normalize_task
from backend.services.workflow_settings import get_workflow_settings

_SESSION_KEY = "cardAgentSession"
_MAX_STORED_MESSAGES = 120


def card_session_enabled(ws: Optional[Dict[str, Any]] = None) -> bool:
    if ws is None:
        ws = get_workflow_settings()
    return bool(ws.get("enableCardSessionContinuity", True))


def _session_blob(task: Dict[str, Any]) -> Dict[str, Any]:
    raw = task.get(_SESSION_KEY)
    if not isinstance(raw, dict):
        raw = {}
        task[_SESSION_KEY] = raw
    return raw


def clear_card_session(task: Dict[str, Any]) -> None:
    task.pop(_SESSION_KEY, None)


def should_resume_card_session(task: Dict[str, Any], *, role: str) -> bool:
    if not card_session_enabled() or role != "Developer":
        return False
    blob = task.get(_SESSION_KEY)
    if not isinstance(blob, dict):
        return False
    messages = blob.get("messages")
    return isinstance(messages, list) and len(messages) >= 2


def load_card_session_messages(task: Dict[str, Any]) -> Optional[List[Dict[str, Any]]]:
    blob = _session_blob(task)
    messages = blob.get("messages")
    if not isinstance(messages, list) or len(messages) < 2:
        return None
    try:
        return copy.deepcopy(messages)
    except Exception:
        return None


def _serialize_messages(messages: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for msg in messages[-_MAX_STORED_MESSAGES :]:
        if not isinstance(msg, dict):
            continue
        item: Dict[str, Any] = {"role": str(msg.get("role") or "user")}
        if msg.get("content") is not None:
            item["content"] = str(msg.get("content") or "")
        if msg.get("tool_calls"):
            try:
                item["tool_calls"] = json.loads(json.dumps(msg.get("tool_calls")))
            except Exception:
                pass
        if msg.get("tool_call_id"):
            item["tool_call_id"] = str(msg.get("tool_call_id"))
        if msg.get("name"):
            item["name"] = str(msg.get("name"))
        out.append(item)
    return out


def save_card_session_messages(
    task: Dict[str, Any],
    messages: Sequence[Dict[str, Any]],
    *,
    user_prompt_snapshot: str = "",
    role: str = "Developer",
) -> None:
    if not card_session_enabled() or role != "Developer":
        return
    normalize_task(task)
    blob = _session_blob(task)
    blob["messages"] = _serialize_messages(messages)
    blob["role"] = role
    if user_prompt_snapshot:
        blob["lastUserPrompt"] = user_prompt_snapshot[:8000]
    try:
        blob["stepCount"] = int(blob.get("stepCount") or 0) + 1
    except (TypeError, ValueError):
        blob["stepCount"] = 1


def build_incremental_step_prompt(task: Dict[str, Any], full_prompt: str) -> str:
    """Append only deltas since the last stored sprint step."""
    blob = task.get(_SESSION_KEY) if isinstance(task.get(_SESSION_KEY), dict) else {}
    prev = str(blob.get("lastUserPrompt") or "").strip()
    full = (full_prompt or "").strip()
    if not prev:
        return full_prompt
    if full == prev:
        return (
            "=== CONTINUATION (same card session) ===\n"
            "Continue from the prior step. Re-read changed files if needed; "
            "apply patches and verify until acceptance criteria are met."
        )
    if full.startswith(prev):
        delta = full[len(prev) :].strip()
        if delta:
            return f"=== CONTINUATION (incremental) ===\n{delta}"
    return (
        "=== CONTINUATION ===\n"
        f"{full[:12000]}"
    )


def maybe_summarize_before_prune(
    messages: MutableSequence[Dict[str, Any]],
    *,
    agent_role: str = "Developer",
) -> None:
    """Optional LLM summary of middle turns before char-based prune drops them."""
    ws = get_workflow_settings()
    if not ws.get("enableCardSessionSummarize", True):
        return
    if len(messages) <= 6:
        return
    from backend.services.llm_context import estimate_messages_chars, message_prune_threshold_chars
    from backend.services.prompt_budget import resolve_ollama_num_ctx

    threshold = message_prune_threshold_chars(resolve_ollama_num_ctx())
    if estimate_messages_chars(messages) <= threshold:
        return
    middle = messages[2:-4]
    if not middle:
        return
    lines: List[str] = []
    for msg in middle[-24:]:
        role = str(msg.get("role") or "")
        content = str(msg.get("content") or "")[:400]
        if content.strip():
            lines.append(f"{role}: {content}")
    if not lines:
        return
    try:
        from backend.services.context_compress import resolve_context_compress_model
        from backend.services.llm_provider import get_chat_provider

        model = resolve_context_compress_model(agent_role)
        provider = get_chat_provider()
        prompt = (
            "Summarize this agent conversation segment for continuation. "
            "Keep file paths, errors, decisions, and next actions.\n\n"
            + "\n".join(lines)
        )
        resp = provider.chat(
            model,
            [{"role": "user", "content": prompt[:16000]}],
            options={"num_predict": 800, "temperature": 0.2},
        )
        summary = (resp.message.content or "").strip() if resp and resp.message else ""
        if not summary:
            return
        messages.insert(
            2,
            {
                "role": "system",
                "content": f"=== CARD SESSION SUMMARY ===\n{summary[:3500]}",
            },
        )
    except Exception:
        return
