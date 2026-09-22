"""Workflow settings API — payload fields match persisted defaults."""

from __future__ import annotations

from fastapi.testclient import TestClient

from backend.api.schemas import WorkflowSettingsPayload
from backend.bootstrap import initialize
from backend.main import app
from backend.services.workflow_settings import (
    DEFAULT_WORKFLOW_SETTINGS,
    get_workflow_settings,
    reset_workflow_settings,
    save_workflow_settings,
)


def test_workflow_settings_payload_covers_default_keys():
    model_fields = set(WorkflowSettingsPayload.model_fields.keys())
    default_keys = set(DEFAULT_WORKFLOW_SETTINGS.keys())
    missing = default_keys - model_fields
    assert not missing, f"WorkflowSettingsPayload missing keys: {sorted(missing)}"


def test_post_enable_llm_context_compress_persists():
    initialize()
    reset_workflow_settings()
    client = TestClient(app)
    r = client.post("/api/workflow/settings", json={"enableLlmContextCompress": True})
    assert r.status_code == 200
    assert r.json()["workflowSettings"]["enableLlmContextCompress"] is True
    ws = get_workflow_settings()
    assert ws.get("enableLlmContextCompress") is True


def test_post_simulation_settings_persist():
    initialize()
    reset_workflow_settings()
    client = TestClient(app)
    r = client.post(
        "/api/workflow/settings",
        json={
            "confirmSimulationFallback": False,
            "simulationConfirmSeconds": 30,
            "simulationAutoAccept": True,
            "simulationAutoUseExistingFile": False,
        },
    )
    assert r.status_code == 200
    ws = r.json()["workflowSettings"]
    assert ws.get("confirmSimulationFallback") is False
    assert ws.get("simulationConfirmSeconds") == 30
    assert ws.get("simulationAutoAccept") is True
    assert ws.get("simulationAutoUseExistingFile") is False
    stored = get_workflow_settings()
    assert stored.get("simulationAutoUseExistingFile") is False


def test_post_execution_profile_persists(tmp_path):
    from backend import state
    from backend.config import DEFAULT_BOARD
    from backend.services.project_file import PROJECT_FILE_NAME, read_project_file
    from backend.services.project_service import save_current_project_state

    workspace = tmp_path / "exec-profile"
    workspace.mkdir()
    state.CURRENT_PROJECT_ID = "exec-profile-api"
    state.PROJECT_NAME = "Exec Profile API"
    state.WORKSPACE_DIR = str(workspace)
    state.SHARED_BOARD = {k: [] for k in DEFAULT_BOARD}
    save_current_project_state(persist_board=False)

    initialize()
    reset_workflow_settings()
    client = TestClient(app)
    r = client.post("/api/workflow/settings", json={"executionProfile": "implementer"})
    assert r.status_code == 200
    assert r.json()["workflowSettings"]["executionProfile"] == "implementer"
    assert get_workflow_settings().get("executionProfile") == "implementer"

    sidecar = read_project_file(str(workspace))
    assert sidecar is not None
    assert sidecar["workflow_settings"]["executionProfile"] == "implementer"
    assert (workspace / PROJECT_FILE_NAME).exists()


def test_post_execution_profile_normalizes_invalid_value():
    initialize()
    reset_workflow_settings()
    client = TestClient(app)
    r = client.post("/api/workflow/settings", json={"executionProfile": "cursor-like"})
    assert r.status_code == 200
    assert r.json()["workflowSettings"]["executionProfile"] == "scrum"
    assert get_workflow_settings().get("executionProfile") == "scrum"
