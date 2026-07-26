"""LLM context budget: tool output truncation and conversation pruning."""

from __future__ import annotations

from typing import Any, Dict, List, MutableSequence, Sequence

from backend.services.workflow_settings import get_workflow_settings

_EPISODE_HEADER = "=== EPISODE SUMMARY ==="
_EPISODE_CAP = 1500


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


def _summarize_removed(msg: Dict[str, Any]) -> str:
    role = str(msg.get("role") or "")
    name = str(msg.get("tool_name") or "")
    content = str(msg.get("content") or "").replace("\n", " ").strip()
    if role == "tool":
        return f"- tool {name or '?'}: {content[:160]}"
    if role == "system" and content.startswith("=== OBSERVATION ==="):
        return f"- observation: {content[20:180].strip()}"
    if role == "system":
        return f"- note: {content[:140]}"
    return f"- {role}: {content[:120]}"


def _merge_episode_summary(existing: str, new_lines: List[str]) -> str:
    body = existing
    if body.startswith(_EPISODE_HEADER):
        body = body[len(_EPISODE_HEADER) :].lstrip("\n")
    merged = (body + "\n" if body else "") + "\n".join(new_lines)
    merged = merged.strip()
    if len(merged) > _EPISODE_CAP:
        merged = "…\n" + merged[-(_EPISODE_CAP - 20) :]
    return f"{_EPISODE_HEADER}\n{merged}"


def prune_messages_if_needed(messages: MutableSequence[Dict[str, Any]]) -> MutableSequence[Dict[str, Any]]:
    """Drop oldest tool messages when conversation exceeds context budget."""
    if len(messages) <= 2:
        return messages

    ws = get_workflow_settings()
    from backend.services.prompt_budget import resolve_ollama_num_ctx

    num_ctx = resolve_ollama_num_ctx()
    threshold = message_prune_threshold_chars(num_ctx)
    if estimate_messages_chars(messages) <= threshold:
        return messages

    enable_episode = ws.get("enableEpisodeSummary", True)
    preserved_head = 2
    pruned = 0
    removed_summaries: List[str] = []
    while len(messages) > preserved_head + 1 and estimate_messages_chars(messages) > threshold:
        removed = messages.pop(preserved_head)
        pruned += 1
        if enable_episode:
            line = _summarize_removed(removed if isinstance(removed, dict) else {})
            if line:
                removed_summaries.append(line)
        if (
            isinstance(removed, dict)
            and removed.get("role") == "tool"
            and preserved_head < len(messages)
        ):
            nxt = messages[preserved_head]
            if isinstance(nxt, dict) and nxt.get("role") == "system" and "Tool '" in str(
                nxt.get("content", "")
            ):
                extra = messages.pop(preserved_head)
                pruned += 1
                if enable_episode:
                    removed_summaries.append(_summarize_removed(extra))

    if pruned:
        if enable_episode and removed_summaries:
            # Fold into existing episode summary or insert new one
            episode_idx = None
            for i in range(preserved_head, min(len(messages), preserved_head + 4)):
                content = str(messages[i].get("content") or "")
                if content.startswith(_EPISODE_HEADER):
                    episode_idx = i
                    break
            summary = _merge_episode_summary(
                str(messages[episode_idx].get("content") or "") if episode_idx is not None else "",
                removed_summaries[-40:],
            )
            if episode_idx is not None:
                messages[episode_idx] = {"role": "system", "content": summary}
            else:
                messages.insert(preserved_head, {"role": "system", "content": summary})
        else:
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
