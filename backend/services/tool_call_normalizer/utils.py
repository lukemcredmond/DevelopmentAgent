"""Shared text/argument normalization helpers for tool call parsing."""

from __future__ import annotations

import json
import re
from typing import Any, Dict, Optional, Set

from backend.services.tool_call_normalizer.canonical import CanonicalToolCall, MAX_RECOVERED_TOOL_CALLS


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


def extract_json_object_substring(text: str) -> Optional[str]:
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
    sub = extract_json_object_substring(text)
    if sub:
        try:
            parsed = json.loads(sub)
            return dict(parsed) if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            pass
    return {}


def allowed(name: str, allowed_tool_names: Set[str]) -> bool:
    return name in allowed_tool_names


def call_from_name_args(
    name: str,
    args: Any,
    allowed_tool_names: Set[str],
    *,
    call_id: str = "",
    index: int = 0,
) -> Optional[CanonicalToolCall]:
    if not name or not allowed(name, allowed_tool_names):
        return None
    arguments = normalize_tool_arguments(args)
    return CanonicalToolCall(
        id=call_id or f"call_{index}",
        name=name,
        arguments=arguments,
    )


def parse_tool_entry(
    obj: Any,
    allowed_tool_names: Set[str],
    *,
    index: int = 0,
) -> Optional[CanonicalToolCall]:
    if not isinstance(obj, dict):
        return None
    fn = obj.get("function")
    if isinstance(fn, dict):
        name = str(fn.get("name") or "").strip()
        args = fn.get("arguments")
        if args is None:
            args = fn.get("parameters")
        return call_from_name_args(name, args, allowed_tool_names, index=index)
    name = str(obj.get("name") or obj.get("tool") or "").strip()
    if name:
        args = obj.get("arguments")
        if args is None:
            args = obj.get("parameters")
        if args is None:
            args = obj.get("args")
        return call_from_name_args(name, args, allowed_tool_names, index=index)
    return None


def shorthand_single_tool(
    obj: dict,
    allowed_tool_names: Set[str],
    *,
    index: int = 0,
) -> Optional[CanonicalToolCall]:
    """Object like {"read_file": {"path": "a.dart"}} with one registered tool key."""
    keys = [k for k in obj.keys() if isinstance(k, str) and allowed(k, allowed_tool_names)]
    if len(keys) != 1:
        return None
    name = keys[0]
    payload = obj.get(name)
    if isinstance(payload, dict):
        return call_from_name_args(name, payload, allowed_tool_names, index=index)
    return None


def cap_calls(calls: list[CanonicalToolCall]) -> list[CanonicalToolCall]:
    return calls[:MAX_RECOVERED_TOOL_CALLS]


def looks_like_raw_tool_markup(content: str) -> bool:
    """True when the model dumped tool-call markup instead of native tool_calls."""
    lower = (content or "").lower()
    return (
        "<tool_call>" in lower
        or "<function=" in lower
        or "</tool_call>" in lower
        or "[tool_calls]" in lower
        or "<invoke" in lower
    )
