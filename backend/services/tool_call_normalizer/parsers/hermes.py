"""Hermes-style <tool_call>{JSON}</tool_call> parser (vLLM-compatible)."""

from __future__ import annotations

import json
import re
from typing import Set

from backend.services.tool_call_normalizer.canonical import CanonicalToolCall
from backend.services.tool_call_normalizer.utils import cap_calls, parse_tool_entry

_TOOL_CALL_RE = re.compile(
    r"<tool_call>\s*([\s\S]*?)\s*</tool_call>",
    re.IGNORECASE,
)


class HermesXmlJsonParser:
    name = "hermes_xml"
    priority = 10

    def detect(self, text: str) -> bool:
        return "<tool_call>" in (text or "").lower()

    def parse(self, text: str, allowed: Set[str]) -> list[CanonicalToolCall]:
        if not text or not allowed:
            return []
        calls: list[CanonicalToolCall] = []
        for match in _TOOL_CALL_RE.finditer(text):
            payload = (match.group(1) or "").strip()
            if not payload:
                continue
            try:
                parsed = json.loads(payload)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, list):
                for item in parsed:
                    call = parse_tool_entry(item, allowed, index=len(calls))
                    if call:
                        call.id = f"call_{len(calls)}"
                        calls.append(call)
            elif isinstance(parsed, dict):
                call = parse_tool_entry(parsed, allowed, index=len(calls))
                if call:
                    call.id = f"call_{len(calls)}"
                    calls.append(call)
        return cap_calls(calls)
