"""Prompt size helpers scaled to Ollama num_ctx."""

from __future__ import annotations

DEFAULT_NUM_CTX = 32768


def sprint_file_context_max_chars(num_ctx: int) -> int:
    """Max chars for pre-loaded sprint file context (~60% of token budget as chars)."""
    return min(12000, max(2000, (num_ctx // 4) * 3))


def truncate_brief(brief: str, num_ctx: int, max_chars: int = 6000) -> str:
    """Truncate project brief to fit context budget."""
    budget = min(max_chars, num_ctx * 2)
    if len(brief) <= budget:
        return brief
    return brief[: budget - 40] + "\n...[brief truncated for context budget]\n"


def skills_context_max_chars(num_ctx: int) -> int:
    return min(8000, max(2000, num_ctx))


def workspace_file_list_cap(num_ctx: int) -> int:
    return 30 if num_ctx >= 8192 else 15


def semantic_sprint_context_max_chars(num_ctx: int) -> int:
    """Budget for semantic index chunks in sprint pre-load."""
    return min(6000, max(1500, sprint_file_context_max_chars(num_ctx) // 2))
