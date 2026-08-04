"""LLM context budget: tool output truncation and conversation pruning."""

from __future__ import annotations

from typing import Any, Dict, List, MutableSequence, Optional, Sequence

from backend.services.workflow_settings import get_workflow_settings

_EPISODE_HEADER = "=== EPISODE SUMMARY ==="
_EPISODE_CAP = 1500


def max_tool_output_chars_for_llm() -> int:
    """Per tool message part budget — scales with num_ctx; 0 = auto (maximize within context)."""
    ws = get_workflow_settings()
    raw = ws.get("maxToolOutputCharsForLlm")
    explicit = int(raw if raw not in (None, "") else 32000)
    from backend.services.prompt_budget import resolve_ollama_num_ctx

    num_ctx = resolve_ollama_num_ctx()
    auto_cap = min(2_000_000, max(100_000, num_ctx * 32))
    if explicit <= 0:
        return auto_cap
    if explicit <= 8000:
        return max(500, explicit)
    return min(2_000_000, max(explicit, num_ctx * 6))


def prepare_tool_output_parts(
    tool_name: str,
    tool_output: str,
    *,
    path: Optional[str] = None,
) -> List[str]:
    """Format (optional read_file focus) then chunk if extremely large."""
    from backend.services.tool_output_chunks import chunk_tool_output

    cap = max_tool_output_chars_for_llm()
    text = str(tool_output or "")

    if tool_name == "read_file" and path:
        from backend import state
        from backend.agents.task_context import get_task_lane
        from backend.services.tool_output_focus import format_read_file_for_llm

        task_lane = get_task_lane(state.ACTIVE_SPRINT_TASK_ID or "") if state.ACTIVE_SPRINT_TASK_ID else ""
        text = format_read_file_for_llm(
            path,
            text,
            agent_role=state.ACTIVE_SPRINT_AGENT,
            task_lane=task_lane,
        )

    if tool_name == "run_command" and "## Problems" in text and len(text) > cap:
        problems_idx = text.find("## Problems")
        output_idx = text.find("## Output", problems_idx)
        if output_idx >= 0:
            head = text[: output_idx + len("## Output\n")]
            raw_tail = text[output_idx + len("## Output\n") :]
            tail_cap = max(500, cap - len(head) - 80)
            if len(raw_tail) <= tail_cap:
                return [head + raw_tail]
            tail_parts = chunk_tool_output("run_command", raw_tail, tail_cap)
            return [head + tail_parts[0]] + tail_parts[1:]

    return chunk_tool_output(tool_name, text, cap)


def truncate_tool_output_for_llm(tool_name: str, tool_output: str, *, path: Optional[str] = None) -> str:
    """Shrink tool output before appending to the LLM conversation."""
    parts = prepare_tool_output_parts(tool_name, tool_output, path=path)
    if len(parts) == 1:
        return parts[0]
    return "\n\n".join(parts)


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


def _index_of_last_assistant(messages: Sequence[Dict[str, Any]]) -> int:
    for i in range(len(messages) - 1, 1, -1):
        if str(messages[i].get("role") or "") == "assistant":
            return i
    tail_keep = 14
    return max(2, len(messages) - tail_keep)


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
    ws = get_workflow_settings()
    if ws.get("enableMessageHistoryPrune", True) is False:
        return messages
    if len(messages) <= 2:
        return messages

    from backend.services.prompt_budget import resolve_ollama_num_ctx

    num_ctx = resolve_ollama_num_ctx()
    threshold = message_prune_threshold_chars(num_ctx)
    if estimate_messages_chars(messages) <= threshold:
        return messages

    enable_episode = ws.get("enableEpisodeSummary", True)
    preserved_head = 2
    keep_from = _index_of_last_assistant(messages)
    pruned = 0
    removed_summaries: List[str] = []
    while len(messages) > preserved_head + 1 and estimate_messages_chars(messages) > threshold:
        if preserved_head >= keep_from:
            break
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


_PROGRESS_MARKER = "=== STEP PROGRESS (prompt unchanged) ==="
_BUNDLE_PREFIX = "=== PROMPT_SECTION_BUNDLE"
_STEP_RECAP_MARKER = "=== STEP RECAP (local model aid) ==="
_STEP_GOAL_MARKER = "=== STEP GOAL (this sprint step) ==="


def fingerprint_llm_messages(messages: Sequence[Dict[str, Any]]) -> str:
    import hashlib
    import json

    parts: List[str] = []
    for msg in messages:
        role = str(msg.get("role") or "")
        content = str(msg.get("content") or "")
        if content.startswith(_PROGRESS_MARKER):
            continue
        if content.startswith(_STEP_RECAP_MARKER) or content.startswith(_STEP_GOAL_MARKER):
            continue
        if content.startswith("=== OBSERVATION ==="):
            continue
        if content.startswith(_BUNDLE_PREFIX):
            parts.append(f"{role}:bundle:{len(content)}:{content[:120]}")
            continue
        if role == "tool":
            name = str(msg.get("tool_name") or msg.get("name") or "tool")
            digest = hashlib.sha256(content.encode("utf-8", errors="replace")).hexdigest()[:16]
            parts.append(f"tool:{name}:{len(content)}:{digest}")
            continue
        if role == "assistant":
            digest = hashlib.sha256(content.encode("utf-8", errors="replace")).hexdigest()[:16]
            parts.append(f"assistant:{len(content)}:{digest}")
            continue
        parts.append(f"{role}:{len(content)}:{content[:200]}")
    blob = json.dumps(parts, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _tail_tool_summaries(messages: Sequence[Dict[str, Any]], *, max_items: int = 6) -> List[str]:
    lines: List[str] = []
    for msg in reversed(messages):
        role = str(msg.get("role") or "")
        content = str(msg.get("content") or "").strip()
        if not content:
            continue
        if role == "tool":
            name = str(msg.get("name") or msg.get("tool_name") or "tool")
            cap = max_tool_output_chars_for_llm()
            preview = content if len(content) <= cap else content[: cap - 40] + "\n...[tail in tool message above]\n"
            lines.append(f"- tool {name}:\n{preview}")
        elif role == "assistant":
            lines.append(f"- assistant: {content[:2000]}")
        if len(lines) >= max_items:
            break
    return list(reversed(lines))


def maybe_inject_unchanged_prompt_progress(
    messages: List[Dict[str, Any]],
    *,
    iteration: int,
    last_fingerprint: str,
) -> tuple[str, bool]:
    fp = fingerprint_llm_messages(messages)
    if iteration <= 1 or not last_fingerprint or fp != last_fingerprint:
        return fp, False
    summaries = _tail_tool_summaries(messages)
    body = "\n".join(summaries) if summaries else "(no tool/assistant messages in history yet)"
    messages.append(
        {
            "role": "system",
            "content": (
                f"{_PROGRESS_MARKER}\n"
                f"LLM iteration {iteration}: the task prompt bundle matches the previous iteration.\n"
                "Use the tool results below — do not repeat identical tool arguments.\n"
                "If a tool was blocked by fingerprint, change approach or edit files before retrying.\n\n"
                f"Recent step output:\n{body}\n"
            ),
        }
    )
    return fingerprint_llm_messages(messages), True


def format_prompt_bundle_system_content(bundle_name: str, block: str) -> str:
    return f"{_BUNDLE_PREFIX} ({bundle_name}) ===\n{block.strip()}\n"
