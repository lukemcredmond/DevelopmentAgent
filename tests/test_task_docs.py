"""Unified docs/tasks/{id}/ paths, legacy fallbacks, and README import."""

from __future__ import annotations

import json
from pathlib import Path

from backend import state
from backend.agents.task_context import get_task_lane
from backend.bootstrap import initialize
from backend.services import card_ledger as ledger
from backend.services.card_ledger import load_tasks, seed_ledger_from_task
from backend.services.task_docs import (
    legacy_spec_markdown_path,
    migrate_legacy_task_docs,
    task_qa_markdown_path,
    task_spec_markdown_path,
)
from backend.services.task_spec_import import import_cards_from_task_specs
from backend.services.task_spec_markdown import ensure_task_spec_for_work
from backend.services.workflow_settings import reset_workflow_settings


def _empty_board():
    state.SHARED_BOARD = {
        lane: []
        for lane in (
            "Backlog",
            "In Progress",
            "Needs PO",
            "Needs User",
            "QA",
            "Done",
            "Features",
            "Refinement",
            "Code Review",
            "Blocked",
        )
    }


def test_unified_doc_paths():
    assert task_spec_markdown_path("TASK-9") == "docs/tasks/TASK-9/README.md"
    assert task_qa_markdown_path("TASK-9") == "docs/tasks/TASK-9/qa.md"
    assert legacy_spec_markdown_path("TASK-9") == "docs/tasks/TASK-9-spec.md"


def test_migrate_legacy_spec_and_allhands_ledger(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "WORKSPACE_DIR", str(tmp_path))
    spec = tmp_path / "docs" / "tasks" / "TASK-OLD-spec.md"
    spec.parent.mkdir(parents=True)
    spec.write_text("# Task TASK-OLD — Specification\n\n## Overview\n- **Title:** Legacy\n", encoding="utf-8")
    old = tmp_path / ".allhands" / "cards" / "TASK-OLD"
    old.mkdir(parents=True)
    (old / "notes.md").write_text("## ideation\nUse provider.\n", encoding="utf-8")
    (old / "tasks.json").write_text(
        json.dumps([{"id": 1, "desc": "Wire screen", "status": "pending", "result": ""}]),
        encoding="utf-8",
    )
    migrate_legacy_task_docs("TASK-OLD")
    dest = tmp_path / "docs" / "tasks" / "TASK-OLD"
    assert (dest / "README.md").read_text(encoding="utf-8").startswith("# Task TASK-OLD")
    assert "provider" in (dest / "notes.md").read_text(encoding="utf-8")
    assert "Wire screen" in (dest / "tasks.json").read_text(encoding="utf-8")


def test_load_tasks_from_plan_md_and_legacy_json(tmp_path, monkeypatch):
    initialize()
    reset_workflow_settings()
    monkeypatch.setattr(state, "WORKSPACE_DIR", str(tmp_path))
    dest = tmp_path / "docs" / "tasks" / "T-PLAN"
    dest.mkdir(parents=True)
    (dest / "plan.md").write_text("- [ ] First step\n- [x] Done step — shipped\n", encoding="utf-8")
    items = load_tasks("T-PLAN")
    assert [t["desc"] for t in items] == ["First step", "Done step"]
    assert items[1]["status"] == "done"
    assert items[1]["result"] == "shipped"

    old = tmp_path / ".allhands" / "cards" / "T-JSON"
    old.mkdir(parents=True)
    (old / "tasks.json").write_text(
        json.dumps([{"id": 1, "desc": "From json", "status": "pending", "result": ""}]),
        encoding="utf-8",
    )
    from_json = load_tasks("T-JSON")
    assert from_json[0]["desc"] == "From json"
    assert (tmp_path / "docs" / "tasks" / "T-JSON" / "tasks.json").is_file()


def test_seed_ledger_writes_plan_checklists(tmp_path, monkeypatch):
    initialize()
    reset_workflow_settings()
    monkeypatch.setattr(state, "WORKSPACE_DIR", str(tmp_path))
    task = {
        "id": "T-SEED",
        "title": "Login",
        "description": "Implement login.",
        "acceptanceCriteria": ["Validates email", "Posts /login"],
    }
    seed_ledger_from_task(task)
    plan = ledger.read_file("T-SEED", "plan.md")
    assert "- [ ] Validates email" in plan
    assert any(t["desc"] == "Validates email" for t in load_tasks("T-SEED"))


def test_ensure_reads_legacy_flat_spec(tmp_path, monkeypatch):
    from backend.agents.task_context import init_new_task, normalize_task

    monkeypatch.setattr(state, "WORKSPACE_DIR", str(tmp_path))
    state.VIRTUAL_FILESYSTEM.clear()
    _empty_board()
    task = init_new_task(
        {
            "id": "T-LEGACY-SPEC",
            "title": "From disk",
            "description": "Desc",
            "acceptanceCriteria": ["A", "B"],
        }
    )
    normalize_task(task)
    state.SHARED_BOARD.setdefault("Backlog", []).append(task)
    legacy = tmp_path / "docs" / "tasks" / "T-LEGACY-SPEC-spec.md"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("# Prebuilt from flat spec\n", encoding="utf-8")
    path = ensure_task_spec_for_work(task["id"])
    assert path
    assert "Prebuilt from flat spec" in (state.VIRTUAL_FILESYSTEM.get(path) or Path(tmp_path, *path.split("/")).read_text(encoding="utf-8"))


def test_import_readme_folder(tmp_path, monkeypatch):
    initialize()
    monkeypatch.setattr(state, "WORKSPACE_DIR", str(tmp_path))
    state.VIRTUAL_FILESYSTEM = {}
    _empty_board()
    folder = tmp_path / "docs" / "tasks" / "TASK-README"
    folder.mkdir(parents=True)
    (folder / "README.md").write_text(
        """# Task TASK-README — Specification

## Overview
- **Title:** Folder spec
- **Status:** Backlog
- **Work type:** implementation
- **Requires Dev:** True
- **Requires QA:** True

## Description
Imported from README.md

## Acceptance criteria
1. Reads folder layout
""",
        encoding="utf-8",
    )
    stats = import_cards_from_task_specs()
    assert stats["importedCount"] == 1
    assert get_task_lane("TASK-README") == "Backlog"
