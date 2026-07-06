"""LLM context budget: tool output truncation and conversation pruning."""

from __future__ import annotations

from typing import Any, Dict, List, MutableSequence, Sequence

from backend.services.workflow_settings import get_workflow_settings


def max_tool_output_chars_for_llm() -> int:
    ws = get_workflow_settings()
    return int(ws.get("maxToolOutputCharsForLlm") or 6000)


def message_prune_threshold_chars(num_ctx: int) -> int:
    ws = get_workflow_settings()
    pct = float(ws.get("messagePruneThresholdPct") or 60)
    pct = max(30.0, min(90.0, pct))
    return int(num_ctx * (pct / 100.0) * 4)


def estimate_messages_chars(messages: Sequence[Dict[str, Any]]) -> int:
    total = 0
    for msg in messages:
        content = msg.get("content")
        if isinstance(content, str):
            total += len(content)
        elif content is not None:
            total += len(str(content))
        thinking = msg.get("thinking")
        if isinstance(thinking, str):
            total += len(thinking)
    return total


def truncate_tool_output_for_llm(tool_name: str, tool_output: str) -> str:
    """Shrink tool output before appending to the LLM conversation."""
    cap = max_tool_output_chars_for_llm()
    text = str(tool_output or "")
    if len(text) <= cap:
        return text

    if tool_name == "run_command" and "## Problems" in text:
        problems_idx = text.find("## Problems")
        output_idx = text.find("## Output", problems_idx)
        if output_idx >= 0:
            head = text[: output_idx + len("## Output\n")]
            tail_budget = max(500, cap - len(head) - 80)
            raw_tail = text[output_idx + len("## Output\n") :]
            if len(raw_tail) > tail_budget:
                raw_tail = raw_tail[: tail_budget - 40] + "\n...[command output truncated]\n"
            return head + raw_tail

    if tool_name == "read_file":
        return (
            text[: cap - 120]
            + f"\n...[read_file output truncated at {cap} chars — use start_line/end_line for large files]\n"
        )

    head_len = cap // 2
    tail_len = cap - head_len - 50
    return (
        text[:head_len]
        + f"\n...[truncated {len(text) - cap} chars for LLM context budget]\n"
        + text[-tail_len:]
    )


def prune_messages_if_needed(messages: MutableSequence[Dict[str, Any]]) -> MutableSequence[Dict[str, Any]]:
    """Drop oldest tool messages when conversation exceeds context budget."""
    if len(messages) <= 2:
        return messages

    ws = get_workflow_settings()
    num_ctx = int(ws.get("ollamaNumCtx", 32768))
    threshold = message_prune_threshold_chars(num_ctx)
    if estimate_messages_chars(messages) <= threshold:
        return messages

    preserved_head = 2
    pruned = 0
    while len(messages) > preserved_head + 1 and estimate_messages_chars(messages) > threshold:
        removed = messages.pop(preserved_head)
        pruned += 1
        if removed.get("role") == "tool" and preserved_head < len(messages):
            nxt = messages[preserved_head]
            if nxt.get("role") == "system" and "Tool '" in str(nxt.get("content", "")):
                messages.pop(preserved_head)
                pruned += 1

    if pruned:
        messages.insert(
            preserved_head,
            {
                "role": "system",
                "content": (
                    f"[Context pruned: removed {pruned} older tool message(s) to stay within "
                    f"~{int(ws.get('messagePruneThresholdPct') or 60)}% of num_ctx. "
                    "Re-read files or re-run commands if you need that detail.]"
                ),
            },
        )
    return messages
