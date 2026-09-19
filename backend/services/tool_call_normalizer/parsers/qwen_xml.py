"""Qwen-style <function=name><parameter=k>v</parameter> XML parser."""

from __future__ import annotations

import re
from typing import Any, Dict, Set

from backend.services.tool_call_normalizer.canonical import CanonicalToolCall
from backend.services.tool_call_normalizer.utils import call_from_name_args, cap_calls

_QWEN_FUNCTION_RE = re.compile(
    r"<function=([a-zA-Z_][a-zA-Z0-9_]*)>(.*?)</function>",
    re.DOTALL | re.IGNORECASE,
)
_QWEN_PARAM_RE = re.compile(
    r"<parameter=([a-zA-Z_][a-zA-Z0-9_]*)>\s*(.*?)\s*</parameter>",
    re.DOTALL | re.IGNORECASE,
)


class QwenParameterXmlParser:
    name = "qwen_xml"
    priority = 20

    def detect(self, text: str) -> bool:
        return "<function=" in (text or "").lower()

    def parse(self, text: str, allowed: Set[str]) -> list[CanonicalToolCall]:
        if not text or not allowed:
            return []
        calls: list[CanonicalToolCall] = []
        for match in _QWEN_FUNCTION_RE.finditer(text):
            name = match.group(1)
            body = match.group(2) or ""
            args: Dict[str, Any] = {}
            for param in _QWEN_PARAM_RE.finditer(body):
                args[str(param.group(1))] = str(param.group(2) or "").strip()
            call = call_from_name_args(name, args, allowed, index=len(calls))
            if call:
                calls.append(call)
        return cap_calls(calls)
