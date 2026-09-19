"""Markdown fence tool call parser (**tool** + ```json)."""

from __future__ import annotations

import re
from typing import Set

from backend.services.tool_call_normalizer.canonical import CanonicalToolCall
from backend.services.tool_call_normalizer.utils import call_from_name_args, cap_calls


class MarkdownFenceParser:
    name = "markdown"
    priority = 40

    def detect(self, text: str) -> bool:
        return "**" in (text or "") and "```" in (text or "")

    def parse(self, text: str, allowed: Set[str]) -> list[CanonicalToolCall]:
        if not text or not allowed:
            return []
        calls: list[CanonicalToolCall] = []

        bold_fence = re.compile(
            r"\*\*([a-zA-Z_][a-zA-Z0-9_]*)\*\*\s*```(?:json)?\s*([\s\S]*?)```",
            re.IGNORECASE,
        )
        for m in bold_fence.finditer(text):
            name = m.group(1)
            if name not in allowed:
                continue
            call = call_from_name_args(name, m.group(2).strip() or "{}", allowed, index=len(calls))
            if call:
                calls.append(call)

        line_fence = re.compile(
            r"(?:^|\n)\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\n\s*```(?:json)?\s*([\s\S]*?)```",
            re.MULTILINE,
        )
        for m in line_fence.finditer(text):
            name = m.group(1)
            if name not in allowed:
                continue
            if any(c.name == name for c in calls):
                continue
            call = call_from_name_args(name, m.group(2).strip() or "{}", allowed, index=len(calls))
            if call:
                calls.append(call)

        inline_backtick = re.compile(
            r"\*\*([a-zA-Z_][a-zA-Z0-9_]*)\*\*\s*`(\{[\s\S]*?\})`",
            re.IGNORECASE,
        )
        for m in inline_backtick.finditer(text):
            name = m.group(1)
            if name not in allowed:
                continue
            if any(c.name == name for c in calls):
                continue
            call = call_from_name_args(name, m.group(2), allowed, index=len(calls))
            if call:
                calls.append(call)

        return cap_calls(calls)
