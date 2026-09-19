"""Native provider response adapters → CanonicalToolCall."""

from __future__ import annotations

import json
from typing import Any, List

from backend.services.tool_call_normalizer.canonical import CanonicalToolCall
from backend.services.tool_call_normalizer.utils import normalize_tool_arguments


def _canonical_from_name_args(
    name: str,
    args: Any,
    *,
    call_id: str,
    index: int,
) -> CanonicalToolCall | None:
    if not name:
        return None
    if isinstance(args, dict):
        arguments = dict(args)
    else:
        arguments = normalize_tool_arguments(args)
    return CanonicalToolCall(
        id=call_id or f"call_{index}",
        name=str(name),
        arguments=arguments,
    )


def normalize_openai_message(message: dict) -> List[CanonicalToolCall]:
    """Parse OpenAI Chat Completions message (tool_calls + legacy function_call)."""
    calls: List[CanonicalToolCall] = []
    for index, tc in enumerate(message.get("tool_calls") or []):
        if not isinstance(tc, dict):
            continue
        fn = tc.get("function") or {}
        args = fn.get("arguments")
        if isinstance(args, str):
            try:
                parsed = json.loads(args)
            except json.JSONDecodeError:
                parsed = normalize_tool_arguments(args)
        else:
            parsed = args
        call = _canonical_from_name_args(
            str(fn.get("name") or ""),
            parsed,
            call_id=str(tc.get("id") or f"call_{index}"),
            index=index,
        )
        if call:
            calls.append(call)

    fn_call = message.get("function_call")
    if not calls and isinstance(fn_call, dict):
        args = fn_call.get("arguments")
        call = _canonical_from_name_args(
            str(fn_call.get("name") or ""),
            args,
            call_id="call_0",
            index=0,
        )
        if call:
            calls.append(call)
    return calls


def normalize_ollama_message(msg: Any) -> List[CanonicalToolCall]:
    """Parse Ollama message object or dict."""
    if isinstance(msg, dict):
        raw_calls = msg.get("tool_calls")
    else:
        raw_calls = getattr(msg, "tool_calls", None) if msg is not None else None

    calls: List[CanonicalToolCall] = []
    for index, tc in enumerate(raw_calls or []):
        if isinstance(tc, dict):
            fn = tc.get("function") or {}
            name = fn.get("name")
            args = fn.get("arguments") or {}
            call_id = tc.get("id")
        else:
            fn = getattr(tc, "function", None)
            name = getattr(fn, "name", None) if fn is not None else None
            args = getattr(fn, "arguments", None) if fn is not None else {}
            call_id = getattr(tc, "id", None)
        call = _canonical_from_name_args(
            str(name or ""),
            args,
            call_id=str(call_id or f"call_{index}"),
            index=index,
        )
        if call:
            calls.append(call)
    return calls


def normalize_anthropic_message(message: dict) -> List[CanonicalToolCall]:
    """Parse Anthropic Messages API assistant content blocks (tool_use)."""
    calls: List[CanonicalToolCall] = []
    content = message.get("content")
    if not isinstance(content, list):
        return calls
    for index, block in enumerate(content):
        if not isinstance(block, dict):
            continue
        if block.get("type") != "tool_use":
            continue
        name = block.get("name")
        args = block.get("input") or {}
        call = _canonical_from_name_args(
            str(name or ""),
            args,
            call_id=str(block.get("id") or f"call_{index}"),
            index=index,
        )
        if call:
            calls.append(call)
    return calls


def canonical_to_provider_tool_calls(
    calls: List[CanonicalToolCall],
) -> list:
    """Convert canonical calls to ProviderToolCall instances."""
    from backend.services.llm_provider import ProviderToolCall, ToolFunction

    return [
        ProviderToolCall(
            id=c.id,
            function=ToolFunction(name=c.name, arguments=dict(c.arguments)),
        )
        for c in calls
    ]
