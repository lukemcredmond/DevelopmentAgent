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


def test_set_project_brief_ignores_placeholder_overwrite(tmp_path, monkeypatch):
    from backend.services.brief_service import resolve_brief_for_sprint

    monkeypatch.setenv("ALLHANDS_HOME", str(tmp_path))
    initialize()
    state.PROJECT_BRIEF = "Real product brief about meals"
    state.PROJECT_ORIGINAL_BRIEF = "Real product brief about meals"
    set_project_brief("brief", source="user")
    assert state.PROJECT_BRIEF == "Real product brief about meals"
    assert resolve_brief_for_sprint("brief") == "Real product brief about meals"


def test_original_brief_set_once(tmp_path, monkeypatch):
    monkeypatch.setenv("ALLHANDS_HOME", str(tmp_path))
    initialize()
    state.CURRENT_PROJECT_ID = "orig-brief"
    state.PROJECT_NAME = "Orig"
    state.PROJECT_BRIEF = ""
    state.PROJECT_ORIGINAL_BRIEF = ""
    state.WORKSPACE_DIR = str(tmp_path / "origws")
    state.SHARED_BOARD = {"Backlog": [], "In Progress": [], "Done": [], "QA": []}
    set_project_brief("First real brief about the product", source="user")
    assert state.PROJECT_ORIGINAL_BRIEF == "First real brief about the product"
    set_project_brief("Edited brief with more details about the product", source="user")
    assert state.PROJECT_BRIEF == "Edited brief with more details about the product"
    assert state.PROJECT_ORIGINAL_BRIEF == "First real brief about the product"
    proj = state.storage.load_project("orig-brief")
    assert proj["original_brief"] == "First real brief about the product"


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


def test_load_project_hydrates_plan_from_sidecar_when_sqlite_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("ALLHANDS_HOME", str(tmp_path))
    initialize()
    ws = tmp_path / "sidecar-ws"
    ws.mkdir()
    plan = "## Summary\nSidecar-only plan\n"
    state.CURRENT_PROJECT_ID = "sidecar-plan"
    state.PROJECT_NAME = "Sidecar"
    state.PROJECT_BRIEF = "Brief"
    state.PROJECT_PLAN_OUTLINE = plan
    state.WORKSPACE_DIR = str(ws)
    state.SHARED_BOARD = {"Backlog": [], "In Progress": [], "Done": [], "QA": []}
    save_current_project_state()

    proj = state.storage.load_project("sidecar-plan")
    state.storage.save_project(
        "sidecar-plan",
        proj["name"],
        proj["brief"],
        proj["workspace_dir"],
        proj["board_state"],
        proj["files"],
        proj["po_skills"],
        proj["dev_skills"],
        proj.get("cr_skills", []),
        proj["qa_skills"],
        proj["po_model"],
        proj["dev_model"],
        proj["cr_model"],
        proj["qa_model"],
        proj.get("po_backup_model") or "",
        proj.get("dev_backup_model") or "",
        proj.get("cr_backup_model") or "",
        proj.get("qa_backup_model") or "",
        plan_outline="",
        persist_board=False,
        original_brief=proj.get("original_brief") or "",
    )

    state.PROJECT_PLAN_OUTLINE = ""
    assert load_project_into_state("sidecar-plan")
    assert "Sidecar-only plan" in state.PROJECT_PLAN_OUTLINE
    reloaded = state.storage.load_project("sidecar-plan")
    assert "Sidecar-only plan" in (reloaded.get("plan_outline") or "")


def test_api_state_includes_plan_after_sidecar_only_drift(tmp_path, monkeypatch):
    monkeypatch.setenv("ALLHANDS_HOME", str(tmp_path))
    initialize()
    ws = tmp_path / "drift-ws"
    ws.mkdir()
    plan = "## Summary\nDrift plan\n"
    state.CURRENT_PROJECT_ID = "drift-plan"
    state.PROJECT_NAME = "Drift"
    state.PROJECT_BRIEF = "Brief"
    state.PROJECT_PLAN_OUTLINE = plan
    state.WORKSPACE_DIR = str(ws)
    state.SHARED_BOARD = {"Backlog": [], "In Progress": [], "Done": [], "QA": []}
    save_current_project_state()

    proj = state.storage.load_project("drift-plan")
    state.storage.save_project(
        "drift-plan",
        proj["name"],
        proj["brief"],
        proj["workspace_dir"],
        proj["board_state"],
        proj["files"],
        proj["po_skills"],
        proj["dev_skills"],
        proj.get("cr_skills", []),
        proj["qa_skills"],
        proj["po_model"],
        proj["dev_model"],
        proj["cr_model"],
        proj["qa_model"],
        proj.get("po_backup_model") or "",
        proj.get("dev_backup_model") or "",
        proj.get("cr_backup_model") or "",
        proj.get("qa_backup_model") or "",
        plan_outline="",
        persist_board=False,
        original_brief=proj.get("original_brief") or "",
    )

    state.PROJECT_PLAN_OUTLINE = ""
    load_project_into_state("drift-plan")

    client = TestClient(app)
    res = client.get("/api/state")
    assert res.status_code == 200
    body = res.json()
    assert "Drift plan" in body.get("projectPlanOutline", "")
