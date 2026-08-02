"""Generated spec markdown per task — card JSON is source of truth."""

from __future__ import annotations

import hashlib
import re
from typing import Any, Dict, List, Optional

from backend.agents.task_context import coerce_task_text, normalize_acceptance_criteria
from backend.services.task_qa_markdown import humanize_field

SPEC_PATH_PREFIX = "docs/tasks"
SPEC_MARKDOWN_MAX_CHARS = 12000


def task_spec_markdown_path(task_id: str) -> str:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in str(task_id))[:80]
    return f"{SPEC_PATH_PREFIX}/{safe}-spec.md"


def _scope_section(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [coerce_task_text(v).strip() for v in value if coerce_task_text(v).strip()]
    text = coerce_task_text(value).strip()
    if not text:
        return []
    return [ln.strip() for ln in text.splitlines() if ln.strip()]


def build_task_spec_markdown(task: Dict[str, Any]) -> str:
    task_id = str(task.get("id") or "?")
    title = humanize_field(task.get("title"), max_len=200) or "(untitled)"
    status = humanize_field(task.get("status"), max_len=40) or "?"
    work_type = humanize_field(task.get("workType"), max_len=40) or "implementation"
    ac = normalize_acceptance_criteria(task.get("acceptanceCriteria"))

    lines: List[str] = [
        f"# Task {task_id} — Specification",
        "",
        "## Overview",
        f"- **Title:** {title}",
        f"- **Status:** {status}",
        f"- **Work type:** {work_type}",
        f"- **Requires Dev:** {task.get('requiresDev', True)}",
        f"- **Requires QA:** {task.get('requiresQa', True)}",
    ]
    if task.get("featureId"):
        lines.append(f"- **Feature epic:** {task['featureId']}")
    if task.get("parentTaskId"):
        lines.append(f"- **Parent task:** {task['parentTaskId']}")
    if task.get("specVersion") is not None:
        lines.append(f"- **Spec version:** {task.get('specVersion')}")
    lines.append("")

    story = coerce_task_text(task.get("userStory")).strip()
    lines.append("## User story")
    lines.append(story if story else "_Not set — add on the card._")
    lines.append("")

    desc = coerce_task_text(task.get("description")).strip()
    lines.append("## Description")
    lines.append(desc if desc else "_Empty._")
    lines.append("")

    lines.append("## Acceptance criteria")
    if ac:
        for i, item in enumerate(ac, start=1):
            lines.append(f"{i}. {item}")
    else:
        lines.append("_None — add testable criteria before implementation._")
    lines.append("")

    in_scope = _scope_section(task.get("scope"))
    out_scope = _scope_section(task.get("outOfScope"))
    lines.append("## Scope")
    if in_scope:
        lines.extend(f"- {s}" for s in in_scope)
    else:
        lines.append("_In scope not listed._")
    lines.append("")
    lines.append("## Out of scope")
    if out_scope:
        lines.extend(f"- {s}" for s in out_scope)
    else:
        lines.append("_None listed._")
    lines.append("")

    test_plan = coerce_task_text(task.get("testPlan")).strip()
    lines.append("## Test plan")
    lines.append(test_plan if test_plan else "_Not set — add verify steps or commands._")
    lines.append("")

    expected = coerce_task_text(task.get("expectedSummary")).strip()
    lines.append("## Expected result")
    lines.append(expected if expected else "_Not set — derived from title, description, and AC._")
    lines.append("")

    lines.append("## Acceptance verification")
    ver_rows = task.get("acVerification") or []
    if ver_rows:
        for row in ver_rows:
            if not isinstance(row, dict):
                continue
            crit = humanize_field(row.get("criterion"), max_len=200)
            exp = humanize_field(row.get("expected") or crit, max_len=200)
            act = humanize_field(row.get("actual"), max_len=300) or "—"
            met = row.get("met")
            mark = "?" if met is None else ("yes" if met else "no")
            lines.append(f"- [{mark}] **{crit}**")
            lines.append(f"  - Expected: {exp}")
            lines.append(f"  - Actual: {act}")
    elif ac:
        for i, item in enumerate(ac, start=1):
            lines.append(f"- [ ] {i}. {item}")
    else:
        lines.append("_No acceptance criteria._")
    lines.append("")

    actual = coerce_task_text(task.get("actualSummary")).strip()
    lines.append("## Actual result")
    lines.append(actual if actual else "_Not recorded yet — updated after QA / verification._")
    lines.append("")

    blocked = [str(b) for b in (task.get("blockedBy") or []) if b]
    lines.append("## Dependencies")
    if blocked:
        lines.extend(f"- Blocked by task: `{b}`" for b in blocked)
    else:
        lines.append("_No blockers._")
    deps = task.get("dependencyOutcomes") or []
    for outcome in deps[-5:]:
        if isinstance(outcome, dict):
            lines.append(
                f"- Completed `{outcome.get('taskId', '?')}`: "
                f"{humanize_field(outcome.get('summary'), max_len=120)}"
            )
    lines.append("")

    from backend.agents.task_context import build_dod_block

    dod = build_dod_block().strip()
    lines.append("## Definition of Done (project)")
    if dod:
        lines.append(dod.replace("=== DEFINITION OF DONE (project) ===", "").strip())
    else:
        lines.append("_Project DoD not configured in Workflow settings._")
    lines.append("")

    lines.append("## Links")
    files = task.get("files") or []
    if files:
        for f in files[:12]:
            if isinstance(f, dict) and f.get("path"):
                lines.append(f"- File: `{f['path']}` ({f.get('action', 'touched')})")
            elif isinstance(f, str):
                lines.append(f"- File: `{f}`")
    qa_path = str(task.get("qaMarkdownPath") or "").strip()
    if qa_path:
        lines.append(f"- Working notes: `{qa_path}`")
    lines.append(f"- This spec: `{task_spec_markdown_path(task_id)}`")
    lines.append("")

    text = "\n".join(lines)
    if len(text) > SPEC_MARKDOWN_MAX_CHARS:
        text = text[: SPEC_MARKDOWN_MAX_CHARS - 40] + "\n\n…(truncated)\n"
    return text


def read_task_spec_markdown_for_prompt(task: Dict[str, Any], *, max_chars: int = 5000) -> str:
    from backend import state

    path = str(task.get("specMarkdownPath") or "").strip()
    if not path:
        path = task_spec_markdown_path(str(task.get("id") or ""))
    content = state.VIRTUAL_FILESYSTEM.get(path)
    if not content:
        content = build_task_spec_markdown(task)
    content = (content or "").strip()
    if len(content) > max_chars:
        content = content[: max_chars - 30] + "\n…(truncated)\n"
    return content


def update_task_spec_markdown(task_id: str) -> Optional[str]:
    import os

    from backend import state
    from backend.agents.task_context import find_task_by_id, normalize_task

    task = find_task_by_id(str(task_id))
    if not task:
        return None
    normalize_task(task)
    from backend.services.card_delivery import sync_card_delivery_fields

    sync_card_delivery_fields(task)
    content = build_task_spec_markdown(task)
    content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]
    prev_hash = str(task.get("_specContentHash") or "")
    if content_hash != prev_hash:
        task["specVersion"] = int(task.get("specVersion") or 0) + 1
        task["_specContentHash"] = content_hash

    path = task_spec_markdown_path(str(task.get("id") or task_id))
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
    task["specMarkdownPath"] = safe_path
    return safe_path


def sync_task_spec_docs(task_id: str) -> None:
    """Update spec markdown for one card (no-op if missing)."""
    try:
        update_task_spec_markdown(str(task_id))
    except Exception:
        pass
