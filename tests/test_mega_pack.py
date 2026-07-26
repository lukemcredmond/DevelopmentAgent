"""Mega pack: AC gate, recovery mode, API token, MCP probe markers."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from backend import state
from backend.agents.task_context import init_new_task, normalize_task
from backend.bootstrap import initialize
from backend.services.api_auth import (
    AllHandsApiTokenMiddleware,
    resolve_discord_bot_token,
    resolve_discord_webhook_url,
)
from backend.services.sprint_service import qa_gate_blocks_done
from backend.services.sprint_session import _build_recovery_from_session
from backend.services.workflow_settings import reset_workflow_settings, save_workflow_settings


def test_recovery_includes_sprint_mode():
    rec = _build_recovery_from_session(
        {
            "taskId": "T-1",
            "taskTitle": "Work",
            "lane": "In Progress",
            "agent": "Developer",
            "sprintMode": "auto",
            "diagnosticsFile": "",
            "lastEvent": "x",
        }
    )
    assert rec["sprintMode"] == "auto"
    assert "auto" in (rec.get("suggestedAction") or "").lower()


def test_ac_checklist_blocks_done():
    initialize()
    reset_workflow_settings()
    save_workflow_settings({"requireAcChecklistForDone": True, "requireCleanLint": False})
    task = init_new_task(
        {
            "id": "T-AC-1",
            "title": "AC gate",
            "description": "d",
            "acceptanceCriteria": ["A", "B"],
            "acChecklist": [True, False],
            "qaEvidence": {
                "playbookRun": True,
                "passed": True,
                "results": [{"outcome": "ok"}],
            },
        }
    )
    normalize_task(task)
    # Satisfy test evidence helper if needed
    task["qaEvidence"] = {
        "playbookRun": True,
        "passed": True,
        "userOverride": False,
        "results": [{"command": "echo", "outcome": "ok", "toolSuccess": True}],
    }
    with patch(
        "backend.services.sprint_service._qa_has_test_evidence",
        return_value=True,
    ):
        blocked, reason = qa_gate_blocks_done(task)
    assert blocked is True
    assert "Acceptance criteria" in reason

    task["acChecklist"] = [True, True]
    with patch(
        "backend.services.sprint_service._qa_has_test_evidence",
        return_value=True,
    ):
        blocked2, _ = qa_gate_blocks_done(task)
    assert blocked2 is False


def test_discord_env_overrides(monkeypatch):
    monkeypatch.setenv("ALLHANDS_DISCORD_BOT_TOKEN", "env-bot-token")
    monkeypatch.setenv("ALLHANDS_DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/1/x")
    assert resolve_discord_bot_token("settings-token") == "env-bot-token"
    assert "webhooks" in resolve_discord_webhook_url("https://other.example/hook")


def test_api_token_middleware_rejects(monkeypatch):
    monkeypatch.setenv("ALLHANDS_API_TOKEN", "secret-token")
    from starlette.applications import Starlette
    from starlette.responses import PlainTextResponse
    from starlette.routing import Route
    from starlette.testclient import TestClient

    async def ok(_request):
        return PlainTextResponse("ok")

    app = Starlette(routes=[Route("/api/state", ok), Route("/", ok)])
    app.add_middleware(AllHandsApiTokenMiddleware)
    client = TestClient(app)
    assert client.get("/api/state").status_code == 401
    assert (
        client.get("/api/state", headers={"Authorization": "Bearer secret-token"}).status_code
        == 200
    )
    assert client.get("/").status_code == 200


def test_mcp_probe_empty():
    initialize()
    reset_workflow_settings()
    save_workflow_settings({"mcpServers": []})
    from backend.services.mcp_tools import probe_mcp_servers

    out = probe_mcp_servers()
    assert out.get("ok") is True
    assert out.get("servers") == []


def test_ui_mega_pack_markers():
    root = Path(__file__).resolve().parents[1]
    app = (root / "frontend" / "src" / "components" / "TaskDetailModal.tsx").read_text(
        encoding="utf-8"
    )
    assert "ac-checklist" in app
    assert "retrieval-feedback-banner" in app
    assert "recovery-resume-step" in (
        root / "frontend" / "src" / "App.tsx"
    ).read_text(encoding="utf-8")
    assert "recovery-resume-auto" in (
        root / "frontend" / "src" / "App.tsx"
    ).read_text(encoding="utf-8")
    panel = (root / "frontend" / "src" / "components" / "WorkflowPanel.tsx").read_text(
        encoding="utf-8"
    )
    assert "workflow-tabs" in panel
    assert "mcp-actions" in panel
    assert "Test MCP" in panel
    assert "requireAcChecklistForDone" in panel
    recovery = (root / "frontend" / "src" / "components" / "BoardRecoveryPanel.tsx").read_text(
        encoding="utf-8"
    )
    assert "board-restore-confirm" in recovery
    ci = (root / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "pytest" in ci
    assert "npm test" in ci
    readme = (root / "README.md").read_text(encoding="utf-8")
    assert "ALLHANDS_API_TOKEN" in readme
    assert "Raise min score" in readme
