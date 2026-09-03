"""Rebuild board cards from generated docs/tasks/*-spec.md files."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from backend import state
from backend.agents.task_context import find_task_by_id, init_new_task
from backend.services.board_lanes import BASE_LANES, normalize_board_lanes
from backend.services.task_qa_markdown import task_qa_markdown_path
from backend.services.task_spec_markdown import SPEC_PATH_PREFIX, task_spec_markdown_path

_TITLE_RE = re.compile(r"^#\s+Task\s+(\S+)\s+[—–-]\s+", re.IGNORECASE)
_OVERVIEW_RE = re.compile(r"^-\s+\*\*([^*]+):\*\*\s*(.*)$")
_PLACEHOLDER_PREFIXES = (
    "_not set",
    "_empty",
    "_none",
    "_no ",
    "_in scope not",
)
_KNOWN_LANES = set(BASE_LANES) | {"Pending Approval", "Code Review"}


def _is_placeholder(text: str) -> bool:
    stripped = (text or "").strip()
    if not stripped:
        return True
    lower = stripped.lower()
    return any(lower.startswith(p) for p in _PLACEHOLDER_PREFIXES)


def _section_map(markdown: str) -> Dict[str, str]:
    chunks = re.split(r"(?m)^##\s+", markdown)
    sections: Dict[str, str] = {}
    for chunk in chunks[1:]:
        lines = chunk.splitlines()
        if not lines:
            continue
        name = lines[0].strip().lower()
        sections[name] = "\n".join(lines[1:]).strip()
    return sections


def _overview_fields(section: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for line in (section or "").splitlines():
        match = _OVERVIEW_RE.match(line.strip())
        if not match:
            continue
        out[match.group(1).strip().lower()] = match.group(2).strip()
    return out


def _parse_bool(value: str, default: bool = True) -> bool:
    raw = str(value or "").strip().lower()
    if raw in ("true", "yes", "1"):
        return True
    if raw in ("false", "no", "0"):
        return False
    return default


def _bullet_or_numbered(section: str) -> List[str]:
    items: List[str] = []
    for line in (section or "").splitlines():
        stripped = line.strip()
        if not stripped or _is_placeholder(stripped):
            continue
        numbered = re.match(r"^\d+\.\s+(.*)$", stripped)
        if numbered:
            items.append(numbered.group(1).strip())
            continue
        if stripped.startswith("- "):
            items.append(stripped[2:].strip())
    return items


def _blocked_by(section: str) -> List[str]:
    ids: List[str] = []
    for line in (section or "").splitlines():
        match = re.search(r"`([^`]+)`", line)
        if match and "blocked by" in line.lower():
            ids.append(match.group(1).strip())
    return ids


def parse_task_spec_markdown(markdown: str, *, fallback_id: str = "") -> Optional[Dict[str, Any]]:
    text = str(markdown or "").strip()
    if not text:
        return None
    first = text.splitlines()[0] if text.splitlines() else ""
    title_match = _TITLE_RE.match(first)
    task_id = title_match.group(1) if title_match else fallback_id
    if not task_id:
        return None
    sections = _section_map(text)
    overview = _overview_fields(sections.get("overview") or "")
    lane = overview.get("status") or "Backlog"
    if lane not in _KNOWN_LANES:
        lane = "Backlog"
    description = sections.get("description") or ""
    story = sections.get("user story") or ""
    test_plan = sections.get("test plan") or ""
    scope_items = _bullet_or_numbered(sections.get("scope") or "")
    out_scope = _bullet_or_numbered(sections.get("out of scope") or "")
    ac = _bullet_or_numbered(sections.get("acceptance criteria") or "")
    task: Dict[str, Any] = {
        "id": task_id,
        "title": overview.get("title") or task_id,
        "status": lane,
        "description": "" if _is_placeholder(description) else description.strip(),
        "userStory": "" if _is_placeholder(story) else story.strip(),
        "testPlan": "" if _is_placeholder(test_plan) else test_plan.strip(),
        "scope": "\n".join(scope_items),
        "outOfScope": "\n".join(out_scope),
        "acceptanceCriteria": ac,
        "workType": overview.get("work type") or "implementation",
        "requiresDev": _parse_bool(overview.get("requires dev", "true")),
        "requiresQa": _parse_bool(overview.get("requires qa", "true")),
        "blockedBy": _blocked_by(sections.get("dependencies") or ""),
        "specMarkdownPath": task_spec_markdown_path(task_id),
        "createdBy": "import",
    }
    if overview.get("feature epic"):
        task["featureId"] = overview["feature epic"]
    if overview.get("parent task"):
        task["parentTaskId"] = overview["parent task"]
    return task


def _id_from_spec_filename(name: str) -> str:
    stem = Path(name).name
    if stem.endswith("-spec.md"):
        return stem[: -len("-spec.md")]
    return stem


def collect_spec_markdown_sources() -> List[Tuple[str, str]]:
    """Return (task_id_hint, markdown) from VFS and workspace disk."""
    found: Dict[str, str] = {}
    prefix = f"{SPEC_PATH_PREFIX}/"
    for key, content in (state.VIRTUAL_FILESYSTEM or {}).items():
        path = str(key).replace("\\", "/")
        if not path.startswith(prefix) or not path.endswith("-spec.md"):
            continue
        text = str(content or "").strip()
        if text:
            found[_id_from_spec_filename(path)] = text
    workspace = Path(state.WORKSPACE_DIR or "")
    disk_dir = workspace / "docs" / "tasks"
    if disk_dir.is_dir():
        for path in sorted(disk_dir.glob("*-spec.md")):
            try:
                text = path.read_text(encoding="utf-8").strip()
            except OSError:
                continue
            if text:
                found.setdefault(_id_from_spec_filename(path.name), text)
    return list(found.items())


def _qa_path_if_present(task_id: str) -> Optional[str]:
    rel = task_qa_markdown_path(task_id)
    if state.VIRTUAL_FILESYSTEM.get(rel) or state.VIRTUAL_FILESYSTEM.get(rel.replace("\\", "/")):
        return rel.replace("\\", "/")
    phys = os.path.join(state.WORKSPACE_DIR or "", rel.replace("/", os.sep))
    if os.path.isfile(phys):
        return rel.replace("\\", "/")
    return None


def _remove_task(task_id: str) -> None:
    for lane, tasks in list(state.SHARED_BOARD.items()):
        if not isinstance(tasks, list):
            continue
        state.SHARED_BOARD[lane] = [
            t for t in tasks if not (isinstance(t, dict) and str(t.get("id")) == task_id)
        ]


def import_cards_from_task_specs(*, overwrite: bool = False) -> Dict[str, Any]:
    """Create/update board cards from docs/tasks specs. Returns import stats."""
    normalize_board_lanes(state.SHARED_BOARD)
    imported: List[str] = []
    skipped: List[str] = []
    overwritten: List[str] = []
    for hint, markdown in collect_spec_markdown_sources():
        parsed = parse_task_spec_markdown(markdown, fallback_id=hint)
        if not parsed:
            continue
        task_id = str(parsed["id"])
        existing = find_task_by_id(task_id)
        if existing and not overwrite:
            skipped.append(task_id)
            continue
        if existing and overwrite:
            _remove_task(task_id)
            overwritten.append(task_id)
        qa_path = _qa_path_if_present(task_id)
        if qa_path:
            parsed["qaMarkdownPath"] = qa_path
        card = init_new_task(parsed)
        lane = str(card.get("status") or "Backlog")
        if lane not in state.SHARED_BOARD:
            lane = "Backlog"
            card["status"] = lane
        state.SHARED_BOARD.setdefault(lane, []).append(card)
        imported.append(task_id)
    return {
        "imported": imported,
        "skipped": skipped,
        "overwritten": overwritten,
        "importedCount": len(imported),
        "skippedCount": len(skipped),
    }
