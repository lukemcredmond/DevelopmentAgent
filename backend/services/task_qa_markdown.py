"""Summarized Q&A markdown per task — human-readable + prompt-fed working notes.

No LLM call: extracts plain Q/A and one-line decisions from task JSON fields.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple

QA_MARKDOWN_MAX_CHARS = 10000
QA_PATH_PREFIX = "docs/tasks"

_PREFERRED_TEXT_KEYS = (
    "question",
    "user_question",
    "userQuestion",
    "answer",
    "message",
    "text",
    "reason",
    "summary",
    "description",
    "content",
    "prompt",
    "body",
    "title",
    "criteria",
)


def task_qa_markdown_path(task_id: str) -> str:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in str(task_id))[:80]
    return f"{QA_PATH_PREFIX}/{safe}-qa.md"


def _try_parse_json(value: str) -> Any:
    text = (value or "").strip()
    if not text:
        return None
    if not (
        (text.startswith("{") and text.endswith("}"))
        or (text.startswith("[") and text.endswith("]"))
    ):
        return None
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None


def humanize_field(value: Any, *, max_len: int = 500) -> str:
    """Turn nested/JSON-ish agent fields into a short plain-language string."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        parts = [humanize_field(v, max_len=max_len // 2) for v in value[:8]]
        parts = [p for p in parts if p]
        text = "; ".join(parts)
        return text[:max_len]
    if isinstance(value, dict):
        for key in _PREFERRED_TEXT_KEYS:
            if key in value and value[key] not in (None, "", [], {}):
                return humanize_field(value[key], max_len=max_len)
        # Fall back to compact key=value pairs (never dump raw JSON braces to the user)
        bits: List[str] = []
        for k, v in list(value.items())[:6]:
            hv = humanize_field(v, max_len=120)
            if hv:
                bits.append(f"{k}: {hv}")
        text = " · ".join(bits) if bits else ""
        return text[:max_len]
    if isinstance(value, str):
        text = value.strip()
        parsed = _try_parse_json(text)
        if parsed is not None:
            return humanize_field(parsed, max_len=max_len)
        # Strip accidental JSON wrappers left as prose
        text = re.sub(r"\s+", " ", text)
        return text[:max_len]
    return str(value).strip()[:max_len]


def extract_qa_pair(payload: Any) -> Tuple[str, str]:
    """Best-effort question + answer from a resolution or nested blob."""
    if isinstance(payload, dict):
        q = humanize_field(
            payload.get("question")
            or payload.get("user_question")
            or payload.get("userQuestion")
            or payload.get("prompt")
            or payload.get("message")
            or payload.get("reason"),
            max_len=400,
        )
        a = humanize_field(
            payload.get("answer")
            or payload.get("response")
            or payload.get("resolution")
            or payload.get("choice"),
            max_len=800,
        )
        if not q and not a:
            # Whole dict may be the question payload
            q = humanize_field(payload, max_len=400)
        return q, a
    if isinstance(payload, str):
        parsed = _try_parse_json(payload)
        if parsed is not None:
            return extract_qa_pair(parsed)
        return humanize_field(payload, max_len=400), ""
    return humanize_field(payload, max_len=400), ""


def build_task_qa_markdown(task: Dict[str, Any]) -> str:
    """Build a compact Q&A / decisions / actions markdown from task fields."""
    task_id = str(task.get("id") or "?")
    title = humanize_field(task.get("title"), max_len=200) or "(untitled)"
    lines: List[str] = [
        f"# Task {task_id} — Working notes",
        "",
        f"**Title:** {title}",
        "",
    ]

    lines.append("## Q&A")
    qa_blocks: List[Tuple[str, str, str]] = []  # q, a, meta

    resolutions = [r for r in (task.get("userResolutions") or []) if isinstance(r, dict)]
    for res in resolutions[-12:]:
        q, a = extract_qa_pair(res)
        if not q and not a:
            continue
        meta_bits = []
        if res.get("timestamp"):
            meta_bits.append(str(res["timestamp"]))
        if res.get("targetLane"):
            meta_bits.append(f"→ {res['targetLane']}")
        qa_blocks.append((q or "(no question text)", a or "(no answer yet)", " · ".join(meta_bits)))

    # Pending Needs User question (unanswered)
    pending = task.get("userQuestion") or task.get("needsUserReason")
    if pending:
        pq, _ = extract_qa_pair(pending) if not isinstance(pending, str) else (humanize_field(pending), "")
        if isinstance(pending, str):
            pq = humanize_field(pending, max_len=400)
        if pq and not any(question_similarity_soft(pq, b[0]) for b in qa_blocks):
            qa_blocks.append((pq, "_Awaiting your answer._", "pending"))

    if qa_blocks:
        for q, a, meta in qa_blocks:
            lines.append(f"### Q: {q}")
            lines.append(f"**A:** {a}")
            if meta:
                lines.append(f"*({meta})*")
            lines.append("")
    else:
        lines.append("_No user Q&A yet._")
        lines.append("")

    decisions = [d for d in (task.get("decisions") or []) if isinstance(d, dict)]
    lines.append("## Decisions (summarized)")
    decision_lines: List[str] = []
    for d in decisions[-20:]:
        agent = humanize_field(d.get("agent"), max_len=40) or "?"
        dtype = humanize_field(d.get("type"), max_len=40) or "note"
        summary = humanize_field(d.get("summary"), max_len=240)
        if not summary and d.get("detail"):
            summary = humanize_field(d.get("detail"), max_len=240)
        if not summary:
            continue
        # Skip pure JSON leftovers
        if summary.startswith("{") and summary.endswith("}"):
            summary = humanize_field(summary, max_len=240)
        if summary:
            decision_lines.append(f"- [{agent}/{dtype}] {summary}")
    if decision_lines:
        lines.extend(decision_lines)
    else:
        lines.append("_No decisions recorded yet._")
    lines.append("")

    lines.append("## Recent actions")
    actions: List[str] = []
    for entry in (task.get("transcript") or [])[-30:]:
        if not isinstance(entry, dict):
            continue
        tool = entry.get("toolName")
        if tool:
            ok = entry.get("toolSuccess")
            status = "ok" if ok is True else ("fail" if ok is False else "?")
            args = entry.get("toolArgs") or {}
            detail = ""
            if isinstance(args, dict):
                if args.get("command"):
                    detail = humanize_field(args["command"], max_len=120)
                elif args.get("path"):
                    detail = humanize_field(args["path"], max_len=120)
                elif args.get("question"):
                    detail = humanize_field(args["question"], max_len=120)
            if not detail:
                detail = humanize_field(entry.get("content"), max_len=80)
            actions.append(f"- `{tool}` ({status})" + (f": {detail}" if detail else ""))
        else:
            role = entry.get("agent") or entry.get("role") or "?"
            content = humanize_field(entry.get("content"), max_len=160)
            if content and role in ("assistant", "system", "Developer", "PO", "QA", "Code Reviewer"):
                actions.append(f"- [{role}] {content}")
    deduped: List[str] = []
    for line in actions[-15:]:
        if not deduped or deduped[-1] != line:
            deduped.append(line)
    if deduped:
        lines.extend(deduped)
    else:
        lines.append("_No recent tool/assistant actions._")
    lines.append("")

    text = "\n".join(lines)
    if len(text) > QA_MARKDOWN_MAX_CHARS:
        text = text[: QA_MARKDOWN_MAX_CHARS - 40] + "\n\n…(truncated)\n"
    return text


def question_similarity_soft(a: str, b: str) -> bool:
    na = re.sub(r"\W+", " ", (a or "").lower()).strip()
    nb = re.sub(r"\W+", " ", (b or "").lower()).strip()
    if not na or not nb:
        return False
    return na == nb or na in nb or nb in na


def update_task_qa_markdown(task_id: str) -> Optional[str]:
    """Write/update docs/tasks/{id}-qa.md; returns path or None."""
    import os

    from backend import state
    from backend.agents.task_context import find_task_by_id, normalize_task

    task = find_task_by_id(str(task_id))
    if not task:
        return None
    normalize_task(task)
    path = task_qa_markdown_path(str(task.get("id") or task_id))
    content = build_task_qa_markdown(task)
    # Write without going through write_workspace_file (avoids decision spam / agent attribution).
    try:
        from backend.workspace.files import resolve_workspace_path

        safe_path = resolve_workspace_path(path)
    except ValueError:
        safe_path = path.replace("\\", "/")
    state.VIRTUAL_FILESYSTEM[safe_path] = content
    phys = os.path.join(state.WORKSPACE_DIR, safe_path.replace("/", os.sep))
    try:
        os.makedirs(os.path.dirname(phys), exist_ok=True)
        with open(phys, "w", encoding="utf-8") as handle:
            handle.write(content)
    except OSError:
        pass
    task["qaMarkdownPath"] = safe_path
    return safe_path


def read_task_qa_markdown_for_prompt(task: Dict[str, Any], *, max_chars: int = 4000) -> str:
    """Load existing Q&A markdown for prompt injection."""
    from backend import state

    path = str(task.get("qaMarkdownPath") or "").strip()
    if not path:
        path = task_qa_markdown_path(str(task.get("id") or ""))
    content = state.VIRTUAL_FILESYSTEM.get(path)
    if not content:
        try:
            from backend.workspace.files import read_workspace_file

            raw = read_workspace_file(path)
            if isinstance(raw, str) and not raw.startswith("Error"):
                content = raw
        except Exception:
            content = None
    if not content or not str(content).strip():
        return ""
    text = str(content).strip()
    if len(text) > max_chars:
        text = text[: max_chars - 20] + "\n…(truncated)"
    return text
