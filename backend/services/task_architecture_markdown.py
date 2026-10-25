"""Generated architecture ADR markdown per task."""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from backend.services.architecture_decisions import get_architecture_decisions
from backend.services.task_docs import task_architecture_markdown_path
from backend.services.task_qa_markdown import humanize_field

ARCH_MARKDOWN_MAX_CHARS = 24000


def build_task_architecture_markdown(task: Dict[str, Any]) -> str:
    task_id = str(task.get("id") or "?")
    title = humanize_field(task.get("title"), max_len=200) or "(untitled)"
    adrs = get_architecture_decisions(task)
    accepted = [d for d in adrs if d.get("status") == "accepted"]
    superseded = [d for d in adrs if d.get("status") == "superseded"]
    proposed = [d for d in adrs if d.get("status") == "proposed"]

    lines: List[str] = [
        f"# Architecture decisions — {task_id}",
        "",
        f"Card: **{title}**",
        "",
        "This document tracks *why* approaches were chosen, alternatives rejected, and how decisions evolved.",
        "",
    ]

    def _render_adr(d: Dict[str, Any], *, include_supersession: bool = True) -> None:
        lines.append(f"### {d.get('title', d.get('topic', 'ADR'))} (`{d.get('topic', '')}`)")
        lines.append(f"- **Status:** {d.get('status', '?')}")
        lines.append(f"- **Author:** {d.get('author', '?')}")
        lines.append(f"- **Updated:** {d.get('updatedAt', d.get('createdAt', '?'))}")
        if d.get("supersedesId"):
            lines.append(f"- **Supersedes:** `{d['supersedesId']}`")
        if include_supersession and d.get("changeNote"):
            lines.append(f"- **Change note:** {d['changeNote']}")
        lines.append("")
        if d.get("context"):
            lines.append("**Context**")
            lines.append(str(d["context"]))
            lines.append("")
        lines.append("**Decision**")
        lines.append(str(d.get("decision") or ""))
        lines.append("")
        alts = d.get("alternatives") or []
        if alts:
            lines.append("**Alternatives considered**")
            for a in alts:
                if isinstance(a, dict):
                    lines.append(
                        f"- **{a.get('name', '?')}** — rejected because {a.get('rejectedBecause', '—')}"
                    )
            lines.append("")
        if d.get("consequences"):
            lines.append("**Consequences**")
            lines.append(str(d["consequences"]))
            lines.append("")
        files = d.get("relatedFiles") or []
        if files:
            lines.append("**Related files**")
            for p in files[:16]:
                lines.append(f"- `{p}`")
            lines.append("")

    lines.append("## Current (accepted)")
    if accepted:
        for d in accepted:
            _render_adr(d)
    else:
        lines.append("_No accepted architecture decisions yet._")
        lines.append("")

    if proposed:
        lines.append("## Proposed")
        for d in proposed:
            _render_adr(d)

    if superseded:
        lines.append("## Superseded")
        for d in superseded:
            _render_adr(d, include_supersession=True)
            note = d.get("supersededByChangeNote") or ""
            if note:
                lines.append(f"_Superseded because:_ {note}")
                lines.append("")

    text = "\n".join(lines)
    if len(text) > ARCH_MARKDOWN_MAX_CHARS:
        text = text[: ARCH_MARKDOWN_MAX_CHARS - 40] + "\n\n…(truncated)\n"
    return text


def update_task_architecture_markdown(task_id: str) -> Optional[str]:
    from backend import state
    from backend.agents.task_context import find_task_by_id, normalize_task

    task = find_task_by_id(str(task_id))
    if not task:
        return None
    normalize_task(task)
    content = build_task_architecture_markdown(task)
    path = task_architecture_markdown_path(str(task.get("id") or task_id))
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
    task["architectureMarkdownPath"] = safe_path
    return safe_path
