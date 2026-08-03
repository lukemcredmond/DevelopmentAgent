"""Detect unchanged LLM request payloads and inject prior tool progress."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Sequence

_PROGRESS_MARKER = "=== STEP PROGRESS (prompt unchanged) ==="
_BUNDLE_PREFIX = "=== PROMPT_SECTION_BUNDLE"


def fingerprint_messages(messages: Sequence[Any]) -> str:
    """Stable hash of roles + content lengths + tail snippet (excludes progress inject noise)."""
    parts: List[str] = []
    for msg in messages:
        if isinstance(msg, dict):
            role = str(msg.get("role") or "")
            content = str(msg.get("content") or "")
            if content.startswith(_PROGRESS_MARKER):
                continue
            if content.startswith(_BUNDLE_PREFIX):
                parts.append(f"{role}:bundle:{len(content)}:{content[:120]}")
                continue
            parts.append(f"{role}:{len(content)}:{content[:200]}")
        else:
            parts.append(str(msg)[:200])
    blob = json.dumps(parts, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _tail_tool_summaries(messages: Sequence[Any], *, max_items: int = 3) -> List[str]:
    lines: List[str] = []
    for msg in reversed(messages):
        if not isinstance(msg, dict):
            continue
        role = str(msg.get("role") or "")
        content = str(msg.get("content") or "").strip()
        if not content:
            continue
        if role == "tool":
            name = str(msg.get("name") or msg.get("tool_name") or "tool")
            lines.append(f"- tool {name}: {content[:600]}")
        elif role == "assistant" and content:
            lines.append(f"- assistant: {content[:400]}")
        if len(lines) >= max_items:
            break
    return list(reversed(lines))


def build_unchanged_prompt_progress_message(*, iteration: int, messages: Sequence[Any]) -> str:
    summaries = _tail_tool_summaries(messages)
    body = "\n".join(summaries) if summaries else "(no tool/assistant messages in history yet)"
    return (
        f"{_PROGRESS_MARKER}\n"
        f"LLM iteration {iteration}: the task prompt bundle matches the previous iteration.\n"
        "Use the tool results below — do not repeat identical tool arguments.\n"
        "If a tool was blocked by fingerprint, change approach or edit files before retrying.\n\n"
        f"Recent step output:\n{body}\n"
    )


def maybe_inject_unchanged_prompt_progress(
    messages: List[Dict[str, Any]],
    *,
    iteration: int,
    last_fingerprint: str,
) -> tuple[str, bool]:
    """Append system progress if fingerprint unchanged. Returns (new_fingerprint, injected)."""
    fp = fingerprint_messages(messages)
    if iteration <= 1 or not last_fingerprint or fp != last_fingerprint:
        return fp, False
    messages.append(
        {
            "role": "system",
            "content": build_unchanged_prompt_progress_message(
                iteration=iteration, messages=messages
            ),
        }
    )
    return fingerprint_messages(messages), True


def format_bundle_system_content(bundle_name: str, block: str) -> str:
    return f"{_BUNDLE_PREFIX} ({bundle_name}) ===\n{block.strip()}\n"
