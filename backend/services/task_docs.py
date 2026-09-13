"""Unified per-card docs under docs/tasks/{id}/ for AllHands and other agents."""

from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Optional

TASKS_PREFIX = "docs/tasks"
LEGACY_LEDGER_REL = Path(".allhands") / "cards"

_TASK_ID_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def safe_task_id(task_id: str) -> str:
    cleaned = _TASK_ID_SAFE.sub("_", str(task_id or "").strip())[:80]
    return cleaned or "unknown"


def task_docs_rel_dir(task_id: str) -> str:
    return f"{TASKS_PREFIX}/{safe_task_id(task_id)}"


def task_spec_markdown_path(task_id: str) -> str:
    return f"{task_docs_rel_dir(task_id)}/README.md"


def task_qa_markdown_path(task_id: str) -> str:
    return f"{task_docs_rel_dir(task_id)}/qa.md"


def legacy_spec_markdown_path(task_id: str) -> str:
    return f"{TASKS_PREFIX}/{safe_task_id(task_id)}-spec.md"


def legacy_qa_markdown_path(task_id: str) -> str:
    return f"{TASKS_PREFIX}/{safe_task_id(task_id)}-qa.md"


def _workspace_root() -> Optional[Path]:
    from backend import state

    raw = str(getattr(state, "WORKSPACE_DIR", "") or "").strip()
    if not raw:
        return None
    return Path(raw).expanduser()


def task_docs_dir(task_id: str) -> Optional[Path]:
    root = _workspace_root()
    if root is None:
        return None
    return root / TASKS_PREFIX / safe_task_id(task_id)


def legacy_ledger_dir(task_id: str) -> Optional[Path]:
    root = _workspace_root()
    if root is None:
        return None
    return root / LEGACY_LEDGER_REL / safe_task_id(task_id)


def migrate_legacy_task_docs(task_id: str) -> None:
    """Copy flat specs and .allhands/cards into docs/tasks/{id}/ when missing."""
    dest = task_docs_dir(task_id)
    root = _workspace_root()
    if dest is None or root is None:
        return
    sid = safe_task_id(task_id)
    pairs = [
        (root / TASKS_PREFIX / f"{sid}-spec.md", dest / "README.md"),
        (root / TASKS_PREFIX / f"{sid}-qa.md", dest / "qa.md"),
    ]
    old_ledger = legacy_ledger_dir(task_id)
    if old_ledger is not None and old_ledger.is_dir():
        for name in ("notes.md", "plan.md", "tasks.json", "last_oracle.txt", "task.md"):
            pairs.append((old_ledger / name, dest / name))
    for src, target in pairs:
        try:
            if target.is_file() or not src.is_file():
                continue
            dest.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, target)
        except OSError:
            continue
