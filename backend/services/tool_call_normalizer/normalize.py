"""Unified assistant message normalization."""

from __future__ import annotations

from typing import Any, Iterable, List, Optional, Tuple

from backend.services.tool_call_normalizer.canonical import CanonicalToolCall
from backend.services.tool_call_normalizer.native import canonical_to_provider_tool_calls
from backend.services.tool_call_normalizer.registry import parse_text_tool_calls
from backend.services.tool_call_normalizer.utils import (
    looks_like_raw_tool_markup,
    normalize_tool_arguments,
)


def _get_attr(message: Any, key: str, default: Any = None) -> Any:
    if isinstance(message, dict):
        return message.get(key, default)
    return getattr(message, key, default)


def _set_attrs(message: Any, updates: dict) -> Any:
    model_copy = getattr(message, "model_copy", None)
    if callable(model_copy):
        return model_copy(update=updates)
    if isinstance(message, dict):
        return {**message, **updates}
    for key, value in updates.items():
        try:
            setattr(message, key, value)
        except Exception:
            pass
    return message


def _native_tool_calls_list(message: Any) -> list:
    return _get_attr(message, "tool_calls", None) or []


def normalize_existing_tool_call_arguments(message: Any) -> None:
    """In-place: unwrap/normalize argument strings on native tool_calls."""
    for call in _native_tool_calls_list(message):
        if isinstance(call, dict):
            fn = call.get("function") or {}
            raw = fn.get("arguments")
            if isinstance(raw, str):
                fn["arguments"] = normalize_tool_arguments(raw)
            continue
        fn = getattr(call, "function", None)
        if fn is None:
            continue
        raw = getattr(fn, "arguments", None)
        if isinstance(raw, str):
            normalized = normalize_tool_arguments(raw)
            try:
                fn.arguments = normalized
            except Exception:
                pass


def _usable_native_calls(message: Any) -> List[CanonicalToolCall]:
    calls: List[CanonicalToolCall] = []
    for index, tc in enumerate(_native_tool_calls_list(message)):
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
        calls.append(
            CanonicalToolCall(
                id=str(call_id or f"call_{index}"),
                name=str(name),
                arguments=dict(args or {}),
            )
        )
    return calls


def _parser_hint_from_settings() -> Optional[str]:
    try:
        from backend.services.workflow_settings import get_workflow_settings

        hint = str(get_workflow_settings().get("toolCallParserHint") or "auto").strip().lower()
        return hint if hint and hint != "auto" else None
    except Exception:
        return None


def _text_sources(message: Any) -> list[tuple[str, str]]:
    """Return (field_name, text) pairs to scan for tool markup."""
    sources: list[tuple[str, str]] = []
    content = str(_get_attr(message, "content", None) or "")
    thinking = str(_get_attr(message, "thinking", None) or "")
    if content.strip():
        sources.append(("content", content))
    if thinking.strip():
        sources.append(("thinking", thinking))
    if content.strip() and thinking.strip():
        sources.append(("combined", f"{content}\n{thinking}"))
    return sources


def normalize_assistant_message(
    message: Any,
    allowed_tool_names: Iterable[str],
    *,
    merge_when_markup: bool = True,
) -> Tuple[Any, List[str], str]:
    """
    Normalize native tool args; recover tool_calls from content/thinking when missing.
    Returns (message, recovered_names, recovery_source).
    """
    normalize_existing_tool_call_arguments(message)
    native = _usable_native_calls(message)
    if native:
        return message, [], "native"

    content = str(_get_attr(message, "content", None) or "")
    thinking = str(_get_attr(message, "thinking", None) or "")
    if not merge_when_markup and not content.strip() and not thinking.strip():
        return message, [], ""

    parser_hint = _parser_hint_from_settings()
    recovered: List[CanonicalToolCall] = []
    recovery_source = ""
    for _field, text in _text_sources(message):
        calls, source = parse_text_tool_calls(text, allowed_tool_names, parser_hint=parser_hint)
        if calls:
            recovered = calls
            recovery_source = source
            break

    if not recovered:
        return message, [], ""

    provider_calls = canonical_to_provider_tool_calls(recovered)
    names = [c.name for c in recovered]

    updates: dict = {
        "tool_calls": provider_calls,
        "content": "",
    }
    if looks_like_raw_tool_markup(thinking):
        updates["thinking"] = None

    message = _set_attrs(message, updates)
    return message, names, recovery_source
