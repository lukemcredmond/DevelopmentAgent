"""Tool call text parser protocol."""

from __future__ import annotations

from typing import Protocol, Set, runtime_checkable

from backend.services.tool_call_normalizer.canonical import CanonicalToolCall


@runtime_checkable
class ToolCallTextParser(Protocol):
    name: str
    priority: int

    def detect(self, text: str) -> bool:
        ...

    def parse(self, text: str, allowed: Set[str]) -> list[CanonicalToolCall]:
        ...
