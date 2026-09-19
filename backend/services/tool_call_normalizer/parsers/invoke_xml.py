"""<invoke name="tool"><parameter name="k">v</parameter></invoke> XML parser."""

from __future__ import annotations

import re
from typing import Any, Dict, Set

from backend.services.tool_call_normalizer.canonical import CanonicalToolCall
from backend.services.tool_call_normalizer.utils import call_from_name_args, cap_calls

_INVOKE_RE = re.compile(
    r'<invoke\s+name=["\']([a-zA-Z_][a-zA-Z0-9_]*)["\']\s*>(.*?)</invoke>',
    re.DOTALL | re.IGNORECASE,
)
_PARAM_NAME_RE = re.compile(
    r'<parameter\s+name=["\']([a-zA-Z_][a-zA-Z0-9_]*)["\']\s*>(.*?)</parameter>',
    re.DOTALL | re.IGNORECASE,
)


class InvokeXmlParser:
    name = "invoke_xml"
    priority = 25

    def detect(self, text: str) -> bool:
        return "<invoke" in (text or "").lower()

    def parse(self, text: str, allowed: Set[str]) -> list[CanonicalToolCall]:
        if not text or not allowed:
            return []
        calls: list[CanonicalToolCall] = []
        for match in _INVOKE_RE.finditer(text):
            name = match.group(1)
            body = match.group(2) or ""
            args: Dict[str, Any] = {}
            for param in _PARAM_NAME_RE.finditer(body):
                args[str(param.group(1))] = str(param.group(2) or "").strip()
            call = call_from_name_args(name, args, allowed, index=len(calls))
            if call:
                calls.append(call)
        return cap_calls(calls)
