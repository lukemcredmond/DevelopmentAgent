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
    write_project_file,
)
from backend.services.project_service import save_current_project_state
from backend.storage.project_storage import count_board_tasks


def test_save_writes_project_file_without_cards(tmp_path):
    state.CURRENT_PROJECT_ID = "sol-1"
    state.PROJECT_NAME = "Solution Demo"
    state.PROJECT_BRIEF = "Brief text"
    state.PROJECT_ORIGINAL_BRIEF = "Brief text"
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
    assert data["original_brief"] == "Brief text"
    assert "board_state" not in data
    assert (tmp_path / PROJECT_FILE_NAME).is_file()
    loaded = state.storage.load_project("sol-1")
    assert count_board_tasks(loaded["board_state"]) == 1


def test_restore_keeps_sqlite_cards_when_sidecar_has_none(tmp_path):
    state.CURRENT_PROJECT_ID = "keep-cards"
    state.PROJECT_NAME = "Keep"
    state.PROJECT_BRIEF = "Keep brief"
    state.PROJECT_ORIGINAL_BRIEF = "Keep brief"
    state.WORKSPACE_DIR = str(tmp_path)
    state.SHARED_BOARD = {
        **{k: [] for k in DEFAULT_BOARD},
        "Backlog": [{"id": "T1", "title": "One", "description": "d", "status": "Backlog"}],
    }
    save_current_project_state(force_board=True)
    pid = restore_project_from_file(str(tmp_path))
    loaded = state.storage.load_project(pid)
    assert loaded["name"] == "Keep"
    assert count_board_tasks(loaded["board_state"]) == 1


def test_open_workspace_restores_identity_without_sidecar_cards(tmp_path):
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
    state.PROJECT_BRIEF = "Gone brief"
    state.PROJECT_ORIGINAL_BRIEF = "Gone brief"
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
    assert loaded["brief"] == "Gone brief"
    assert count_board_tasks(loaded["board_state"]) == 0

    client = TestClient(app)
    state.CURRENT_PROJECT_ID = "keep-me"
    again = client.post("/api/projects/open-workspace", json={"workspaceDir": str(orphan_dir)})
    assert again.status_code == 200
    body = again.json()
    assert body["projectId"] == "gone-id"
    assert body["projectName"] == "Gone"


def _seed_project(tmp_path, project_id: str, *, plan_outline: str = "") -> None:
    state.CURRENT_PROJECT_ID = project_id
    state.PROJECT_NAME = "Plan Test"
    state.PROJECT_BRIEF = "Brief"
    state.PROJECT_ORIGINAL_BRIEF = "Brief"
    state.PROJECT_PLAN_OUTLINE = plan_outline
    state.WORKSPACE_DIR = str(tmp_path)
    state.SHARED_BOARD = {k: [] for k in DEFAULT_BOARD}
    save_current_project_state(force_board=True)


def test_restore_project_file_preserves_plan_outline(tmp_path):
    plan = "## Summary\nRestored plan\n"
    _seed_project(tmp_path, "plan-restore", plan_outline=plan)
    pid = restore_project_from_file(str(tmp_path))
    loaded = state.storage.load_project(pid)
    assert "Restored plan" in (loaded.get("plan_outline") or "")


def test_restore_does_not_wipe_sqlite_plan_when_sidecar_empty(tmp_path):
    plan = "## Summary\nKeep sqlite plan\n"
    _seed_project(tmp_path, "plan-keep", plan_outline=plan)
    sidecar = read_project_file(str(tmp_path))
    assert sidecar is not None
    sidecar["plan_outline"] = ""
    write_project_file(str(tmp_path), sidecar)
    pid = restore_project_from_file(str(tmp_path))
    loaded = state.storage.load_project(pid)
    assert "Keep sqlite plan" in (loaded.get("plan_outline") or "")


def test_restore_accepts_projectPlanOutline_alias(tmp_path):
    _seed_project(tmp_path, "plan-alias", plan_outline="")
    sidecar = read_project_file(str(tmp_path))
    assert sidecar is not None
    sidecar.pop("plan_outline", None)
    sidecar["projectPlanOutline"] = "## Summary\nAlias plan\n"
    write_project_file(str(tmp_path), sidecar)
    pid = restore_project_from_file(str(tmp_path))
    loaded = state.storage.load_project(pid)
    assert "Alias plan" in (loaded.get("plan_outline") or "")
