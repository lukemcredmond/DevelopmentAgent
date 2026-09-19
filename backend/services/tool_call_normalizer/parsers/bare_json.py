"""Bare JSON / fenced JSON tool call parser (fallback)."""

from __future__ import annotations

import json
import re
from typing import Any, Set

from backend.services.tool_call_normalizer.canonical import CanonicalToolCall
from backend.services.tool_call_normalizer.utils import (
    cap_calls,
    extract_json_object_substring,
    parse_tool_entry,
    shorthand_single_tool,
    unwrap_llm_text,
)


class BareJsonParser:
    name = "json"
    priority = 50

    def detect(self, text: str) -> bool:
        stripped = unwrap_llm_text(text or "")
        return "{" in stripped or "[" in stripped

    def parse(self, text: str, allowed: Set[str]) -> list[CanonicalToolCall]:
        if not text or not allowed:
            return []
        stripped = unwrap_llm_text(text)
        if not stripped:
            return []

        parsed: Any = None
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            sub = extract_json_object_substring(stripped)
            if sub:
                try:
                    parsed = json.loads(sub)
                except json.JSONDecodeError:
                    parsed = None
            if parsed is None:
                array_match = re.search(r"\[[\s\S]*\]", stripped)
                if array_match:
                    try:
                        parsed = json.loads(array_match.group())
                    except json.JSONDecodeError:
                        parsed = None

        calls: list[CanonicalToolCall] = []
        if isinstance(parsed, list):
            for item in parsed:
                call = parse_tool_entry(item, allowed, index=len(calls))
                if call:
                    calls.append(call)
        elif isinstance(parsed, dict):
            # Legacy OpenAI function_call in content
            fn_call = parsed.get("function_call")
            if isinstance(fn_call, dict):
                call = parse_tool_entry({"function": fn_call}, allowed, index=0)
                if call:
                    calls.append(call)
                    return cap_calls(calls)
            call = parse_tool_entry(parsed, allowed, index=0)
            if call:
                calls.append(call)
            else:
                shorthand = shorthand_single_tool(parsed, allowed, index=0)
                if shorthand:
                    calls.append(shorthand)
        return cap_calls(calls)
