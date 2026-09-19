"""Mistral [TOOL_CALLS] [...] prefix parser."""

from __future__ import annotations

import json
import re
from typing import Set

from backend.services.tool_call_normalizer.canonical import CanonicalToolCall
from backend.services.tool_call_normalizer.utils import cap_calls, parse_tool_entry

_PREFIX_RE = re.compile(r"\[TOOL_CALLS\]\s*", re.IGNORECASE)


class MistralPrefixParser:
    name = "mistral"
    priority = 30

    def detect(self, text: str) -> bool:
        return "[tool_calls]" in (text or "").lower()

    def parse(self, text: str, allowed: Set[str]) -> list[CanonicalToolCall]:
        if not text or not allowed:
            return []
        stripped = _PREFIX_RE.sub("", text.strip(), count=1)
        if not stripped:
            return []
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            array_match = re.search(r"\[[\s\S]*\]", stripped)
            if not array_match:
                return []
            try:
                parsed = json.loads(array_match.group())
            except json.JSONDecodeError:
                return []
        calls: list[CanonicalToolCall] = []
        if isinstance(parsed, list):
            for item in parsed:
                call = parse_tool_entry(item, allowed, index=len(calls))
                if call:
                    calls.append(call)
        elif isinstance(parsed, dict):
            call = parse_tool_entry(parsed, allowed, index=0)
            if call:
                calls.append(call)
        return cap_calls(calls)
