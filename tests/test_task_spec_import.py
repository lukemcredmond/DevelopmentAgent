"""Rebuild cards from docs/tasks spec markdown."""

from pathlib import Path

from backend import state
from backend.agents.task_context import get_task_lane, init_new_task
from backend.bootstrap import initialize
from backend.services.task_spec_import import import_cards_from_task_specs, parse_task_spec_markdown
from backend.services.task_spec_markdown import build_task_spec_markdown


def test_parse_spec_roundtrip():
    initialize()
    task = init_new_task(
        {
            "id": "TASK-SPEC1",
            "title": "Wire shopping list",
            "description": "Connect provider to screen",
            "status": "In Progress",
            "userStory": "As a shopper I see my list",
            "acceptanceCriteria": ["List renders", "Empty state works"],
            "scope": "AisleListScreen",
            "testPlan": "Run widget tests",
            "workType": "implementation",
            "blockedBy": ["TASK-DEP"],
        }
    )
    parsed = parse_task_spec_markdown(build_task_spec_markdown(task))
    assert parsed is not None
    assert parsed["id"] == "TASK-SPEC1"
    assert parsed["title"] == "Wire shopping list"
    assert parsed["status"] == "In Progress"
    assert "List renders" in parsed["acceptanceCriteria"]
    assert "TASK-DEP" in parsed["blockedBy"]
    assert parsed["testPlan"].startswith("Run widget tests")


def test_import_specs_from_workspace_disk(tmp_path):
    initialize()
    state.WORKSPACE_DIR = str(tmp_path)
    state.VIRTUAL_FILESYSTEM = {}
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
    spec_dir = Path(tmp_path) / "docs" / "tasks"
    spec_dir.mkdir(parents=True)
    (spec_dir / "TASK-20FE-spec.md").write_text(
        """# Task TASK-20FE — Specification

## Overview
- **Title:** Integrate ShoppingListProvider
- **Status:** In Progress
- **Work type:** implementation
- **Requires Dev:** True
- **Requires QA:** True

## User story
As a user I see aisles.

## Description
Hook the provider into AisleListScreen.

## Acceptance criteria
1. Provider is used
2. List updates

## Scope
- AisleListScreen

## Out of scope
- Checkout

## Test plan
flutter test

## Dependencies
_No blockers._
""",
        encoding="utf-8",
    )
    stats = import_cards_from_task_specs()
    assert stats["importedCount"] == 1
    assert get_task_lane("TASK-20FE") == "In Progress"
    again = import_cards_from_task_specs()
    assert again["skippedCount"] == 1
    assert again["importedCount"] == 0
