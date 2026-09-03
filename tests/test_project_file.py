"""Workspace allhands.project.json sidecar."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from backend import state
from backend.config import DEFAULT_BOARD
from backend.services.project_file import (
    PROJECT_FILE_NAME,
    read_project_file,
    restore_project_from_file,
)
from backend.services.project_service import save_current_project_state
from backend.storage.project_storage import count_board_tasks


def test_save_writes_project_file(tmp_path):
    state.CURRENT_PROJECT_ID = "sol-1"
    state.PROJECT_NAME = "Solution Demo"
    state.PROJECT_BRIEF = "Brief text"
    state.WORKSPACE_DIR = str(tmp_path)
    state.SHARED_BOARD = {
        **{k: [] for k in DEFAULT_BOARD},
        "Backlog": [{"id": "T1", "title": "One", "description": "d", "status": "Backlog"}],
    }
    save_current_project_state(force_board=True)
    data = read_project_file(str(tmp_path))
    assert data is not None
    assert data["format"] == "allhands-project"
    assert data["id"] == "sol-1"
    assert data["name"] == "Solution Demo"
    assert data["brief"] == "Brief text"
    assert count_board_tasks(data["board_state"]) == 1
    assert (tmp_path / PROJECT_FILE_NAME).is_file()


def test_open_workspace_restores_deleted_sqlite_row(tmp_path):
    from backend.main import app

    state.CURRENT_PROJECT_ID = "keep-me"
    state.PROJECT_NAME = "Keep"
    state.WORKSPACE_DIR = str(tmp_path / "keep")
    Path(state.WORKSPACE_DIR).mkdir()
    state.SHARED_BOARD = {k: [] for k in DEFAULT_BOARD}
    save_current_project_state(force_board=True)

    orphan_dir = tmp_path / "gone"
    orphan_dir.mkdir()
    state.CURRENT_PROJECT_ID = "gone-id"
    state.PROJECT_NAME = "Gone"
    state.WORKSPACE_DIR = str(orphan_dir)
    state.SHARED_BOARD = {
        **{k: [] for k in DEFAULT_BOARD},
        "Backlog": [
            {"id": "T9", "title": "Nine", "description": "d", "status": "Backlog"},
            {"id": "T8", "title": "Eight", "description": "d", "status": "Backlog"},
        ],
    }
    save_current_project_state(force_board=True)
    assert state.storage.delete_project("gone-id")
    assert state.storage.load_project("gone-id") is None
    assert read_project_file(str(orphan_dir)) is not None

    pid = restore_project_from_file(str(orphan_dir))
    assert pid == "gone-id"
    loaded = state.storage.load_project("gone-id")
    assert loaded is not None
    assert loaded["name"] == "Gone"
    assert count_board_tasks(loaded["board_state"]) == 2

    client = TestClient(app)
    state.CURRENT_PROJECT_ID = "keep-me"
    again = client.post("/api/projects/open-workspace", json={"workspaceDir": str(orphan_dir)})
    assert again.status_code == 200
    body = again.json()
    assert body["projectId"] == "gone-id"
    assert body["projectName"] == "Gone"
