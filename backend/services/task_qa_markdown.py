"""Summarized Q&A markdown per task — human-readable + prompt-fed working notes."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

QA_MARKDOWN_MAX_CHARS = 10000
QA_PATH_PREFIX = "docs/tasks"


def task_qa_markdown_path(task_id: str) -> str:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in str(task_id))[:80]
    return f"{QA_PATH_PREFIX}/{safe}-qa.md"


def build_task_qa_markdown(task: Dict[str, Any]) -> str:
    """Build a compact Q&A / decisions / actions markdown from task fields."""
    task_id = str(task.get("id") or "?")
    title = str(task.get("title") or "").strip() or "(untitled)"
    lines: List[str] = [
        f"# Task {task_id} — Working notes",
        "",
        f"**Title:** {title}",
        "",
    ]

    resolutions = [r for r in (task.get("userResolutions") or []) if isinstance(r, dict)]
    lines.append("## Q&A")
    if resolutions:
        for res in resolutions[-12:]:
            q = str(res.get("question") or "").strip()
            a = str(res.get("answer") or "").strip()
            if not q and not a:
                continue
            lines.append(f"### Q: {q[:400]}")
            lines.append(f"**A:** {a[:800]}")
            ts = res.get("timestamp") or ""
            lane = res.get("targetLane") or ""
            if ts or lane:
                lines.append(f"*({ts} → {lane})*")
            lines.append("")
    else:
        lines.append("_No user Q&A yet._")
        lines.append("")

    decisions = [d for d in (task.get("decisions") or []) if isinstance(d, dict)]
    lines.append("## Decisions (summarized)")
    if decisions:
        for d in decisions[-20:]:
            agent = str(d.get("agent") or "?")
            dtype = str(d.get("type") or "note")
            summary = str(d.get("summary") or "").strip()[:240]
            if summary:
                lines.append(f"- [{agent}/{dtype}] {summary}")
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
                    detail = str(args["command"])[:120]
                elif args.get("path"):
                    detail = str(args["path"])[:120]
            content = str(entry.get("content") or "")[:80]
            bit = detail or content
            actions.append(f"- `{tool}` ({status})" + (f": {bit}" if bit else ""))
        else:
            role = entry.get("agent") or entry.get("role") or "?"
            content = str(entry.get("content") or "").strip()
            if content and role in ("assistant", "system", "Developer", "PO", "QA", "Code Reviewer"):
                actions.append(f"- [{role}] {content[:160]}")
    # de-dupe consecutive identical lines
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
        # try disk via sync key
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
