"""Brief and plan outline persistence and document API."""

import json

from fastapi.testclient import TestClient

from backend import state
from backend.bootstrap import initialize, load_project_into_state
from backend.main import app
from backend.services.brief_service import set_project_brief
from backend.services.project_service import save_current_project_state
from backend.services.workflow_settings import save_workflow_settings


def test_plan_outline_persists_across_reload(tmp_path, monkeypatch):
    monkeypatch.setenv("ALLHANDS_HOME", str(tmp_path))
    initialize()
    state.CURRENT_PROJECT_ID = "persist-plan"
    state.PROJECT_NAME = "Test"
    state.PROJECT_BRIEF = "Brief body"
    state.PROJECT_PLAN_OUTLINE = "## Summary\nMy plan\n"
    state.WORKSPACE_DIR = str(tmp_path / "ws")
    state.SHARED_BOARD = {"Backlog": [], "In Progress": [], "Done": [], "QA": []}
    save_current_project_state()

    state.PROJECT_PLAN_OUTLINE = ""
    assert load_project_into_state("persist-plan")
    assert "## Summary" in state.PROJECT_PLAN_OUTLINE


def test_set_project_brief_ignores_empty_overwrite(tmp_path, monkeypatch):
    monkeypatch.setenv("ALLHANDS_HOME", str(tmp_path))
    initialize()
    state.PROJECT_BRIEF = "Saved brief"
    set_project_brief("", source="user")
    assert state.PROJECT_BRIEF == "Saved brief"


def test_patch_project_documents_api(tmp_path, monkeypatch):
    monkeypatch.setenv("ALLHANDS_HOME", str(tmp_path))
    initialize()
    state.CURRENT_PROJECT_ID = "doc-api"
    state.PROJECT_NAME = "Doc"
    state.PROJECT_BRIEF = ""
    state.PROJECT_PLAN_OUTLINE = ""
    state.WORKSPACE_DIR = str(tmp_path / "ws2")
    state.SHARED_BOARD = {"Backlog": [], "In Progress": [], "Done": [], "QA": []}
    save_current_project_state()

    client = TestClient(app)
    res = client.patch(
        "/api/project/documents",
        json={"brief": "New brief", "projectPlanOutline": "## Plan\nLine"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["brief"] == "New brief"
    assert "Plan" in body.get("projectPlanOutline", "")

    proj = state.storage.load_project("doc-api")
    assert proj["brief"] == "New brief"
    assert "Plan" in (proj.get("plan_outline") or "")


def test_backlog_preflight_counts_planning_only(tmp_path, monkeypatch):
    from backend.services.backlog_preflight import build_backlog_preflight

    monkeypatch.setenv("ALLHANDS_HOME", str(tmp_path))
    initialize()
    state.SHARED_BOARD = {
        "Backlog": [
            {"id": "T1", "title": "Plan", "workType": "planning", "requiresDev": False},
            {"id": "T2", "title": "Build", "workType": "implementation", "requiresDev": True},
        ],
        "In Progress": [],
    }
    pre = build_backlog_preflight()
    assert pre["implementationReady"] == 1
    assert pre["planningOnly"] == 1
