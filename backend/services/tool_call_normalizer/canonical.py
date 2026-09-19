"""Canonical tool call representation used across parsers and providers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict

MAX_RECOVERED_TOOL_CALLS = 8


@dataclass
class CanonicalToolCall:
    id: str
    name: str
    arguments: Dict[str, Any]
