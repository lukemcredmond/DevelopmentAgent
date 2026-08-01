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
