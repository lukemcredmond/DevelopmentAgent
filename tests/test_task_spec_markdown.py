"""Generated task spec markdown and prompt injection."""

from __future__ import annotations

from pathlib import Path

from backend import state
from backend.agents.task_context import build_task_prompt, init_new_task
from backend.bootstrap import initialize
from backend.services.board_service import append_backlog_tasks
from backend.services.task_spec_markdown import (
    build_task_spec_markdown,
    read_task_spec_markdown_for_prompt,
    task_spec_markdown_path,
    update_task_spec_markdown,
)
from backend.services.workflow_settings import get_workflow_settings


def _clear_board():
    state.SHARED_BOARD.clear()
    for lane in (
        "Features",
        "Backlog",
        "Pending Approval",
        "Refinement",
        "In Progress",
        "Needs User",
        "Needs PO",
        "Code Review",
        "QA",
        "Done",
    ):
        state.SHARED_BOARD[lane] = []


def test_build_task_spec_markdown_sections():
    task = {
        "id": "TASK-SPEC-1",
        "title": "Login button",
        "description": "Add OAuth entry point",
        "status": "Backlog",
        "workType": "implementation",
        "userStory": "As a user I want to sign in so that I can save meals",
        "acceptanceCriteria": ["Button visible", "OAuth flow completes"],
        "scope": "Login screen only",
        "testPlan": "flutter test && manual sign-in",
    }
    md = build_task_spec_markdown(task)
    assert "# Task TASK-SPEC-1 — Specification" in md
    assert "## User story" in md
    assert "As a user" in md
    assert "## Acceptance criteria" in md
    assert "1. Button visible" in md
    assert "## Test plan" in md
    assert "flutter test" in md
    dod = get_workflow_settings().get("definitionOfDone") or []
    if dod:
        assert "## Definition of Done" in md


def test_update_spec_sets_path_and_version(tmp_path, monkeypatch):
    initialize()
    _clear_board()
    monkeypatch.setattr(state, "WORKSPACE_DIR", str(tmp_path))
    state.VIRTUAL_FILESYSTEM.clear()
    task = init_new_task(
        {
            "id": "TASK-SPEC-2",
            "title": "Card",
            "description": "Desc",
            "acceptanceCriteria": ["A", "B"],
            "status": "Backlog",
        }
    )
    state.SHARED_BOARD["Backlog"] = [task]
    path = update_task_spec_markdown("TASK-SPEC-2")
    assert path == task_spec_markdown_path("TASK-SPEC-2")
    assert task.get("specMarkdownPath") == path
    assert int(task.get("specVersion") or 0) >= 1
    assert path in state.VIRTUAL_FILESYSTEM
    phys = Path(tmp_path) / Path(path)
    assert phys.is_file()

    task["userStory"] = "As a dev I want specs"
    update_task_spec_markdown("TASK-SPEC-2")
    assert int(task.get("specVersion") or 0) >= 2


def test_build_task_prompt_includes_spec_block(tmp_path, monkeypatch):
    initialize()
    _clear_board()
    monkeypatch.setattr(state, "WORKSPACE_DIR", str(tmp_path))
    state.VIRTUAL_FILESYSTEM.clear()
    task = init_new_task(
        {
            "id": "TASK-SPEC-3",
            "title": "Prompt spec",
            "description": "D",
            "acceptanceCriteria": ["One", "Two"],
            "status": "In Progress",
        }
    )
    state.SHARED_BOARD["In Progress"] = [task]
    update_task_spec_markdown("TASK-SPEC-3")
    prompt = build_task_prompt(task, "brief")
    assert "TASK SPEC (authoritative" in prompt
    assert "Acceptance criteria" in prompt or "1. One" in prompt
    doc = read_task_spec_markdown_for_prompt(task)
    assert "One" in doc


def test_task_interface_includes_sdd_fields():
    types_path = (
        Path(__file__).resolve().parents[1] / "frontend" / "src" / "types" / "index.ts"
    )
    text = types_path.read_text(encoding="utf-8")
    for field in ("userStory", "specMarkdownPath", "specVersion", "testPlan"):
        assert field in text


def test_append_backlog_tasks_writes_spec_file(tmp_path, monkeypatch):
    initialize()
    _clear_board()
    monkeypatch.setattr(state, "WORKSPACE_DIR", str(tmp_path))
    state.VIRTUAL_FILESYSTEM.clear()
    msg = append_backlog_tasks(
        [
            {
                "title": "New slice",
                "description": "Do one thing",
                "workType": "implementation",
                "acceptanceCriteria": ["Done when X", "Done when Y"],
            }
        ]
    )
    assert "Added" in msg
    backlog = state.SHARED_BOARD.get("Backlog") or []
    assert len(backlog) == 1
    tid = str(backlog[0]["id"])
    assert backlog[0].get("specMarkdownPath")
    path = task_spec_markdown_path(tid)
    assert path in state.VIRTUAL_FILESYSTEM
    assert "New slice" in state.VIRTUAL_FILESYSTEM[path]
