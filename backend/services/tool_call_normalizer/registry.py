"""Ordered parser registry for text-based tool call recovery."""

from __future__ import annotations

from typing import Iterable, Optional, Set

from backend.services.tool_call_normalizer.canonical import CanonicalToolCall
from backend.services.tool_call_normalizer.parsers import DEFAULT_PARSERS
from backend.services.tool_call_normalizer.parsers.base import ToolCallTextParser

_PARSER_BY_NAME = {p.name: p for p in DEFAULT_PARSERS}


def get_parsers(hint: Optional[str] = None) -> list[ToolCallTextParser]:
    """Return parsers in execution order. Optional hint tries that parser first."""
    parsers = sorted(DEFAULT_PARSERS, key=lambda p: p.priority)
    if not hint or hint == "auto":
        return parsers
    preferred = _PARSER_BY_NAME.get(hint)
    if preferred is None:
        return parsers
    rest = [p for p in parsers if p.name != preferred.name]
    return [preferred] + rest


def parse_text_tool_calls(
    text: str,
    allowed_tool_names: Iterable[str],
    *,
    parser_hint: Optional[str] = None,
) -> tuple[list[CanonicalToolCall], str]:
    """
    Parse tool calls from assistant text.
    Returns (calls, parser_name) — parser_name is empty when nothing matched.
    """
    allowed = set(allowed_tool_names)
    if not allowed or not (text or "").strip():
        return [], ""

    for parser in get_parsers(parser_hint):
        if not parser.detect(text):
            continue
        calls = parser.parse(text, allowed)
        if calls:
            return calls, parser.name
    return [], ""
