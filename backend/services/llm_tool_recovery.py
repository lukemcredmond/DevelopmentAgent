"""Recover Ollama tool calls when models emit JSON inside content fences or quotes."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set

MAX_RECOVERED_TOOL_CALLS = 8


@dataclass
class _RecoveredFunction:
    name: str
    arguments: Dict[str, Any]


@dataclass
class RecoveredToolCall:
    """Minimal tool call shape for ScrumAgent._process_tool_calls."""

    function: _RecoveredFunction


def unwrap_llm_text(text: str) -> str:
    """Strip outer whitespace and one layer of quote or markdown fences."""
    s = (text or "").strip()
    if not s:
        return s

    triple_patterns = ("'''", '"""')
    for mark in triple_patterns:
        if s.startswith(mark) and s.endswith(mark) and len(s) >= len(mark) * 2:
            inner = s[len(mark) : -len(mark)].strip()
            if inner:
                return inner

    bt = "```"
    if s.startswith(bt):
        json_fence = re.match(rf"^{bt}json\s*([\s\S]*?)\s*{bt}\s*$", s, re.IGNORECASE)
        if json_fence:
            return json_fence.group(1).strip()
        bare_fence = re.match(rf"^{bt}\s*([\s\S]*?)\s*{bt}\s*$", s)
        if bare_fence:
            return bare_fence.group(1).strip()

    return s


def _extract_json_object_substring(text: str) -> Optional[str]:
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    in_string = False
    escape = False
    quote = ""
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escape:
                escape = False
                continue
            if ch == "\\":
                escape = True
                continue
            if ch == quote:
                in_string = False
            continue
        if ch in ('"', "'"):
            in_string = True
            quote = ch
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


def normalize_tool_arguments(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, dict):
        return dict(raw)
    if not isinstance(raw, str):
        return {}
    text = unwrap_llm_text(raw.strip())
    if not text:
        return {}
    try:
        parsed = json.loads(text)
        return dict(parsed) if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        pass
    sub = _extract_json_object_substring(text)
    if sub:
        try:
            parsed = json.loads(sub)
            return dict(parsed) if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            pass
    return {}


def _allowed(name: str, allowed_tool_names: Set[str]) -> bool:
    return name in allowed_tool_names


def _call_from_name_args(name: str, args: Any, allowed_tool_names: Set[str]) -> Optional[RecoveredToolCall]:
    if not name or not _allowed(name, allowed_tool_names):
        return None
    arguments = normalize_tool_arguments(args)
    return RecoveredToolCall(function=_RecoveredFunction(name=name, arguments=arguments))


def _parse_tool_entry(obj: Any, allowed_tool_names: Set[str]) -> Optional[RecoveredToolCall]:
    if not isinstance(obj, dict):
        return None
    fn = obj.get("function")
    if isinstance(fn, dict):
        name = str(fn.get("name") or "").strip()
        args = fn.get("arguments")
        if args is None:
            args = fn.get("parameters")
        return _call_from_name_args(name, args, allowed_tool_names)
    name = str(obj.get("name") or obj.get("tool") or "").strip()
    if name:
        args = obj.get("arguments")
        if args is None:
            args = obj.get("parameters")
        if args is None:
            args = obj.get("args")
        return _call_from_name_args(name, args, allowed_tool_names)
    return None


def _shorthand_single_tool(obj: dict, allowed_tool_names: Set[str]) -> Optional[RecoveredToolCall]:
    """Object like {"read_file": {"path": "a.dart"}} with one registered tool key."""
    keys = [k for k in obj.keys() if isinstance(k, str) and _allowed(k, allowed_tool_names)]
    if len(keys) != 1:
        return None
    name = keys[0]
    payload = obj.get(name)
    if isinstance(payload, dict):
        return _call_from_name_args(name, payload, allowed_tool_names)
    return None


def recover_tool_calls_from_content(
    content: str,
    allowed_tool_names: Iterable[str],
) -> List[RecoveredToolCall]:
    """Parse fenced/quoted content into executable tool calls (registered names only)."""
    allowed = set(allowed_tool_names)
    if not allowed:
        return []

    text = unwrap_llm_text(content or "")
    if not text:
        return []

    parsed: Any = None
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        sub = _extract_json_object_substring(text)
        if sub:
            try:
                parsed = json.loads(sub)
            except json.JSONDecodeError:
                parsed = None
        if parsed is None:
            array_match = re.search(r"\[[\s\S]*\]", text)
            if array_match:
                try:
                    parsed = json.loads(array_match.group())
                except json.JSONDecodeError:
                    parsed = None

    calls: List[RecoveredToolCall] = []
    if isinstance(parsed, list):
        for item in parsed:
            call = _parse_tool_entry(item, allowed)
            if call:
                calls.append(call)
    elif isinstance(parsed, dict):
        call = _parse_tool_entry(parsed, allowed)
        if call:
            calls.append(call)
        else:
            shorthand = _shorthand_single_tool(parsed, allowed)
            if shorthand:
                calls.append(shorthand)

    return calls[:MAX_RECOVERED_TOOL_CALLS]


def normalize_existing_tool_call_arguments(message: Any) -> None:
    """In-place: unwrap/normalize argument strings on native Ollama tool_calls."""
    tool_calls = getattr(message, "tool_calls", None) or []
    for call in tool_calls:
        fn = getattr(call, "function", None)
        if fn is None:
            continue
        raw = getattr(fn, "arguments", None)
        if isinstance(raw, str):
            normalized = normalize_tool_arguments(raw)
            try:
                fn.arguments = json.dumps(normalized)
            except Exception:
                fn.arguments = json.dumps(normalized, default=str)


def apply_tool_call_recovery(message: Any, allowed_tool_names: Iterable[str]) -> List[str]:
    """
    Normalize native tool args; recover tool_calls from content when missing.
    Returns names of recovered tools (empty if none).
    """
    normalize_existing_tool_call_arguments(message)
    existing = getattr(message, "tool_calls", None) or []
    if existing:
        return []

    content = getattr(message, "content", None) or ""
    recovered = recover_tool_calls_from_content(content, allowed_tool_names)
    if not recovered:
        return []

    try:
        message.tool_calls = recovered  # type: ignore[attr-defined]
    except Exception:
        setattr(message, "tool_calls", recovered)
    try:
        message.content = ""  # type: ignore[attr-defined]
    except Exception:
        setattr(message, "content", "")

    return [c.function.name for c in recovered]
