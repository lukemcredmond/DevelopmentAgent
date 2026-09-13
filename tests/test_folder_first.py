"""Folder-first recents, open-workspace hydration, skill import."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from backend import state
from backend.agents.registry import agent_dev
from backend.config import DEFAULT_BOARD
from backend.main import app
from backend.services.project_file import PROJECT_FILE_NAME, write_project_file
from backend.services.recent_workspaces import load_recents, projects_list_for_client, touch_recent
from backend.services.workspace_open import open_workspace_folder
from backend.storage.project_storage import count_board_tasks


def test_touch_recent_and_client_list(tmp_path):
    touch_recent(project_id="p1", name="One", workspace_dir=str(tmp_path / "a"))
    touch_recent(project_id="p2", name="Two", workspace_dir=str(tmp_path / "b"))
    items = load_recents()
    assert items[0]["projectId"] == "p2"
    assert items[1]["projectId"] == "p1"
    listed = projects_list_for_client()
    assert listed[0]["id"] == "p2"
    assert listed[0]["workspaceDir"] == str(tmp_path / "b")


def test_open_empty_folder_creates_sidecar(tmp_path):
    folder = tmp_path / "fresh"
    folder.mkdir()
    pid = open_workspace_folder(str(folder))
    assert (folder / PROJECT_FILE_NAME).is_file()
    assert pid
    recents = load_recents()
    assert recents[0]["projectId"] == pid
    assert recents[0]["path"] == str(folder)


def test_open_folder_imports_docs_tasks(tmp_path):
    folder = tmp_path / "with-docs"
    spec = folder / "docs" / "tasks" / "TASK-OPEN"
    spec.mkdir(parents=True)
    (spec / "README.md").write_text(
        """# Task TASK-OPEN — Specification

## Overview
- **Title:** Imported card
- **Status:** Backlog
- **Work type:** implementation
- **Requires Dev:** True
- **Requires QA:** True

## Description
From README

## Acceptance criteria
1. Exists
""",
        encoding="utf-8",
    )
    open_workspace_folder(str(folder))
    assert count_board_tasks(state.SHARED_BOARD) >= 1
    found = False
    for tasks in state.SHARED_BOARD.values():
        for card in tasks or []:
            if str(card.get("id")) == "TASK-OPEN":
                found = True
    assert found


def test_open_folder_restores_v1_board_state(tmp_path):
    folder = tmp_path / "v1"
    folder.mkdir()
    write_project_file(
        str(folder),
        {
            "format": "allhands-project",
            "formatVersion": 1,
            "id": "v1-proj",
            "name": "Legacy",
            "brief": "B",
            "original_brief": "B",
            "workspace_dir": str(folder),
            "po_skills": [],
            "dev_skills": [],
            "cr_skills": [],
            "qa_skills": [],
            "po_model": "llama3:8b",
            "dev_model": "qwen2.5-coder:14b",
            "cr_model": "qwen2.5-coder:7b",
            "qa_model": "qwen2.5-coder:7b",
            "board_state": {
                **{k: [] for k in DEFAULT_BOARD},
                "Backlog": [
                    {"id": "T-V1", "title": "From v1", "description": "d", "status": "Backlog"}
                ],
            },
        },
    )
    pid = open_workspace_folder(str(folder))
    assert pid == "v1-proj"
    assert count_board_tasks(state.SHARED_BOARD) == 1


def test_open_workspace_api_and_skill_import(tmp_path):
    source = tmp_path / "srcproj"
    dest = tmp_path / "destproj"
    source.mkdir()
    dest.mkdir()
    (source / "skills").mkdir()
    (source / "skills" / "from_old.md").write_text("# Old skill\n", encoding="utf-8")
    write_project_file(
        str(source),
        {
            "format": "allhands-project",
            "formatVersion": 2,
            "id": "src-id",
            "name": "Src",
            "brief": "",
            "workspace_dir": str(source),
            "po_skills": [],
            "dev_skills": ["from_old.md"],
            "cr_skills": [],
            "qa_skills": [],
            "po_model": "llama3:8b",
            "dev_model": "qwen2.5-coder:14b",
            "cr_model": "qwen2.5-coder:7b",
            "qa_model": "qwen2.5-coder:7b",
        },
    )
    open_workspace_folder(str(dest))
    client = TestClient(app)
    res = client.post("/api/skills/import", json={"sourceWorkspaceDir": str(source)})
    assert res.status_code == 200
    assert "from_old.md" in agent_dev.assigned_skills
    assert (dest / "skills" / "from_old.md").is_file()


def test_agent_tools_panel_has_select_all():
    panel = Path(__file__).resolve().parents[1] / "frontend" / "src" / "components" / "AgentToolsPanel.tsx"
    text = panel.read_text(encoding="utf-8")
    assert "Select all" in text
    assert "selectAllForRole" in text


def test_sidebar_has_no_fake_default_workspace():
    sidebar = Path(__file__).resolve().parents[1] / "frontend" / "src" / "components" / "Sidebar.tsx"
    text = sidebar.read_text(encoding="utf-8")
    assert "Default Project Workspace" not in text
    assert "Open folder" in text
