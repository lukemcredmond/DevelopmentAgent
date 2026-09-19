"""Recover tool calls when models emit JSON/XML inside content fences or quotes.

Thin compatibility facade over tool_call_normalizer.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Sequence, Tuple

from backend.services.tool_call_normalizer import (
    MAX_RECOVERED_TOOL_CALLS,
    looks_like_raw_tool_markup,
    normalize_assistant_message,
    normalize_existing_tool_call_arguments,
    normalize_tool_arguments,
    parse_text_tool_calls,
    unwrap_llm_text,
)
from backend.services.tool_call_normalizer.canonical import CanonicalToolCall


@dataclass
class _RecoveredFunction:
    name: str
    arguments: Dict[str, Any]


@dataclass
class RecoveredToolCall:
    """Minimal tool call shape for ScrumAgent._process_tool_calls."""

    function: _RecoveredFunction


def _canonical_to_recovered(calls: Sequence[CanonicalToolCall]) -> List[RecoveredToolCall]:
    return [
        RecoveredToolCall(
            function=_RecoveredFunction(name=c.name, arguments=dict(c.arguments)),
        )
        for c in calls
    ]


def recover_tool_calls_from_content(
    content: str,
    allowed_tool_names: Iterable[str],
) -> List[RecoveredToolCall]:
    """Parse fenced/quoted content into executable tool calls (registered names only)."""
    calls, _source = parse_text_tool_calls(content, allowed_tool_names)
    return _canonical_to_recovered(calls)


def ollama_tool_calls_from_recovered(
    recovered: Sequence[RecoveredToolCall],
) -> List[Any]:
    """Build Ollama pydantic ToolCall instances (required for Ollama Message validation)."""
    from ollama._types import Message as OllamaMessage

    return [
        OllamaMessage.ToolCall(
            function=OllamaMessage.ToolCall.Function(
                name=c.function.name,
                arguments=dict(c.function.arguments),
            )
        )
        for c in recovered
    ]


def _provider_calls_to_ollama(tool_calls: Sequence[Any]) -> List[Any]:
    from ollama._types import Message as OllamaMessage

    out = []
    for tc in tool_calls:
        if isinstance(tc, dict):
            fn = tc.get("function") or {}
            name = fn.get("name")
            args = fn.get("arguments") or {}
        else:
            fn = getattr(tc, "function", None)
            name = getattr(fn, "name", None) if fn else None
            args = getattr(fn, "arguments", None) if fn else {}
        if not name:
            continue
        if isinstance(args, str):
            args = normalize_tool_arguments(args)
        out.append(
            OllamaMessage.ToolCall(
                function=OllamaMessage.ToolCall.Function(
                    name=str(name),
                    arguments=dict(args or {}),
                )
            )
        )
    return out


def assistant_message_to_chat_dict(message: Any) -> Dict[str, Any]:
    """Serialize assistant message for chat history (dicts only — avoids pydantic ERR)."""
    if isinstance(message, dict):
        return dict(message)
    content = getattr(message, "content", None) or ""
    role = getattr(message, "role", None) or "assistant"
    tool_calls = getattr(message, "tool_calls", None) or []
    if not tool_calls:
        out: Dict[str, Any] = {"role": role, "content": content}
        thinking = getattr(message, "thinking", None)
        if thinking:
            out["thinking"] = thinking
        return out
    serialized: List[Dict[str, Any]] = []
    for tc in tool_calls:
        if isinstance(tc, dict):
            fn = tc.get("function") or {}
            name = fn.get("name")
            args = fn.get("arguments")
            call_id = tc.get("id")
        else:
            fn = getattr(tc, "function", None)
            name = getattr(fn, "name", None) if fn else None
            args = getattr(fn, "arguments", None) if fn else None
            call_id = getattr(tc, "id", None)
        if not name:
            continue
        if isinstance(args, str):
            args = normalize_tool_arguments(args)
        elif not isinstance(args, dict):
            args = normalize_tool_arguments(args)
        entry: Dict[str, Any] = {"function": {"name": name, "arguments": args or {}}}
        if call_id:
            entry["id"] = str(call_id)
        serialized.append(entry)
    out = {"role": role, "content": content}
    if serialized:
        out["tool_calls"] = serialized
    thinking = getattr(message, "thinking", None)
    if thinking:
        out["thinking"] = thinking
    return out


def apply_tool_call_recovery(
    message: Any,
    allowed_tool_names: Iterable[str],
) -> Tuple[List[str], Any]:
    """
    Normalize native tool args; recover tool_calls from content/thinking when missing.
    Returns (tool names, message) — message may be replaced via model_copy.
    """
    message, names, _source = normalize_assistant_message(message, allowed_tool_names)
    if not names:
        return [], message

    model_copy = getattr(message, "model_copy", None)
    if callable(model_copy):
        ollama_calls = _provider_calls_to_ollama(getattr(message, "tool_calls", None) or [])
        if ollama_calls:
            message = message.model_copy(update={"tool_calls": ollama_calls})

    return names, message
