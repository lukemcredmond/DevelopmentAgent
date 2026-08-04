"""Detect assistant text that repeats prior tool output (common SLM failure mode)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence

_MIN_TOOL_BODY_LEN = 80
_ECHO_RATIO = 0.85


@dataclass
class EchoDetection:
    is_echo: bool
    tool_name: Optional[str] = None
    similarity: float = 0.0
    reason: str = ""


def normalize_for_compare(text: str) -> str:
    s = (text or "").strip()
    if not s:
        return ""
    s = re.sub(r"</?tool_response\s*/?>", " ", s, flags=re.IGNORECASE)
    s = re.sub(r"```[\s\S]*?```", " ", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def last_substantive_tool_bodies(
    messages: Sequence[Any],
    *,
    n: int = 2,
    min_len: int = _MIN_TOOL_BODY_LEN,
) -> List[tuple[str, str]]:
    """Return (tool_name, body) for last n tool messages with substantive content."""
    found: List[tuple[str, str]] = []
    for msg in reversed(messages):
        if not isinstance(msg, dict):
            continue
        if str(msg.get("role") or "") != "tool":
            continue
        body = str(msg.get("content") or "").strip()
        if len(body) < min_len:
            continue
        name = str(msg.get("name") or msg.get("tool_name") or "tool")
        found.append((name, body))
        if len(found) >= n:
            break
    return list(reversed(found))


def _overlap_ratio(assistant_norm: str, tool_norm: str) -> float:
    if not assistant_norm or not tool_norm:
        return 0.0
    if len(tool_norm) <= len(assistant_norm):
        if tool_norm in assistant_norm:
            return len(tool_norm) / max(len(assistant_norm), 1)
    if assistant_norm in tool_norm:
        return len(assistant_norm) / max(len(tool_norm), 1)
    # Prefix overlap heuristic for truncated echoes
    shorter, longer = (
        (assistant_norm, tool_norm)
        if len(assistant_norm) <= len(tool_norm)
        else (tool_norm, assistant_norm)
    )
    if len(shorter) >= _MIN_TOOL_BODY_LEN and longer.startswith(shorter[: min(len(shorter), 400)]):
        return len(shorter) / max(len(longer), 1)
    return 0.0


def detect_tool_output_echo(
    assistant_content: str,
    messages: Sequence[Any],
) -> EchoDetection:
    content = (assistant_content or "").strip()
    if not content:
        return EchoDetection(is_echo=False)

    raw_lower = content.lower()
    has_tool_tags = "<tool_response" in raw_lower and "</tool_response>" in raw_lower

    assistant_norm = normalize_for_compare(content)
    if len(assistant_norm) < 40 and not has_tool_tags:
        return EchoDetection(is_echo=False)

    for tool_name, body in reversed(last_substantive_tool_bodies(messages)):
        tool_norm = normalize_for_compare(body)
        if not tool_norm:
            continue
        ratio = _overlap_ratio(assistant_norm, tool_norm)
        if ratio >= _ECHO_RATIO:
            return EchoDetection(
                is_echo=True,
                tool_name=tool_name,
                similarity=round(ratio, 3),
                reason=f"Assistant text overlaps {int(ratio * 100)}% of last {tool_name} output",
            )
        if has_tool_tags:
            inner = re.sub(
                r"(?is).*?<tool_response[^>]*>(.*?)</tool_response>.*",
                r"\1",
                content,
            ).strip()
            inner_norm = normalize_for_compare(inner)
            if inner_norm and _overlap_ratio(inner_norm, tool_norm) >= _ECHO_RATIO:
                return EchoDetection(
                    is_echo=True,
                    tool_name=tool_name,
                    similarity=round(_overlap_ratio(inner_norm, tool_norm), 3),
                    reason=f"<tool_response> body repeats last {tool_name} output",
                )

    if has_tool_tags and len(assistant_norm) >= _MIN_TOOL_BODY_LEN:
        return EchoDetection(
            is_echo=True,
            tool_name=None,
            similarity=0.0,
            reason="Assistant wrapped content in <tool_response> instead of calling a tool",
        )

    return EchoDetection(is_echo=False)


ECHO_REJECTION_MESSAGE = (
    "Do not repeat tool output in assistant text or <tool_response> tags — it is already in "
    "tool messages above. Call apply_patch, write_file, update_board, or another tool."
)
