"""allhands.project.json round-trips models, MCP, prompts, and permissions."""

from __future__ import annotations

from fastapi.testclient import TestClient

from backend import state
from backend.bootstrap import load_project_into_state
from backend.config import DEFAULT_BOARD
from backend.main import app
from backend.services.project_file import (
    DEFAULT_ROLE_MODELS,
    PROJECT_FILE_NAME,
    read_project_file,
    restore_project_from_file,
)
from backend.services.project_service import save_current_project_state
from backend.services.workflow_settings import get_workflow_settings, save_workflow_settings
from backend.services.workspace_open import open_workspace_folder


def _wipe_sqlite_project(project_id: str) -> None:
    state.storage.delete_project(project_id)
    state.storage.set_setting(f"workflow:{project_id}", "")


def test_settings_round_trip_via_project_file(tmp_path):
    workspace = tmp_path / "app"
    workspace.mkdir()
    skills = tmp_path / "skills"
    skills.mkdir()
    pid = "settings-sidecar"
    state.CURRENT_PROJECT_ID = pid
    state.PROJECT_NAME = "Sidecar Settings"
    state.PROJECT_BRIEF = "Brief"
    state.PROJECT_ORIGINAL_BRIEF = "Brief"
    state.WORKSPACE_DIR = str(workspace)
    state.SKILLS_DIR = str(skills)
    state.SHARED_BOARD = {k: [] for k in DEFAULT_BOARD}
    state.PRIMARY_MODELS = {
        "po": "qwen2.5:7b",
        "dev": "qwen2.5-coder:32b",
        "cr": "qwen2.5-coder:14b",
        "qa": "qwen2.5:3b",
    }
    state.BACKUP_MODELS = {
        "po": "llama3:8b",
        "dev": "qwen2.5-coder:7b",
        "cr": "",
        "qa": "",
    }

    save_current_project_state(persist_board=False)
    save_workflow_settings(
        {
            "requireToolApproval": True,
            "toolApprovalTools": ["write_file", "run_command"],
            "commandAllowlist": ["pytest", "ruff check"],
            "discordBotAllowedUserIds": ["111", "222"],
            "executionProfile": "implementer",
            "mcpServers": [
                {
                    "name": "filesystem",
                    "command": "__allhands_missing_mcp__",
                    "args": [],
                }
            ],
            "agentPrompts": {
                "Developer": {
                    "system": "You are the project Developer.",
                    "stepInstructions": "Ship the card.",
                }
            },
            "agentTools": {"Developer": ["read_file", "write_file"]},
            "llmApiKey": "secret-must-not-land-on-disk",
        }
    )

    data = read_project_file(str(workspace))
    assert data is not None
    assert data["po_model"] == "qwen2.5:7b"
    assert data["dev_model"] == "qwen2.5-coder:32b"
    assert data["cr_model"] == "qwen2.5-coder:14b"
    assert data["qa_model"] == "qwen2.5:3b"
    assert data["po_backup_model"] == "llama3:8b"
    assert data["dev_backup_model"] == "qwen2.5-coder:7b"
    assert data["skills_dir"] == str(skills)
    ws_file = data["workflow_settings"]
    assert ws_file["requireToolApproval"] is True
    assert ws_file["toolApprovalTools"] == ["write_file", "run_command"]
    assert ws_file["commandAllowlist"] == ["pytest", "ruff check"]
    assert ws_file["discordBotAllowedUserIds"] == ["111", "222"]
    assert ws_file["mcpServers"][0]["name"] == "filesystem"
    assert ws_file["agentPrompts"]["Developer"]["system"] == "You are the project Developer."
    assert ws_file["agentTools"]["Developer"] == ["read_file", "write_file"]
    assert ws_file["executionProfile"] == "implementer"
    assert ws_file.get("llmApiKey") in ("", None)
    raw = (workspace / PROJECT_FILE_NAME).read_text(encoding="utf-8")
    assert "secret-must-not-land-on-disk" not in raw

    _wipe_sqlite_project(pid)
    state.PRIMARY_MODELS = {
        "po": "gemma-4-q4km:26b",
        "dev": "gemma-4-q4km:26b",
        "cr": "gemma-4-q4km:26b",
        "qa": "gemma-4-q4km:26b",
    }
    state.BACKUP_MODELS = {}
    state.SKILLS_DIR = str(tmp_path / "other-skills")

    restored = restore_project_from_file(str(workspace))
    assert restored == pid
    assert load_project_into_state(pid)

    row = state.storage.load_project(pid)
    assert row["po_model"] == "qwen2.5:7b"
    assert row["dev_model"] == "qwen2.5-coder:32b"
    assert row["cr_model"] == "qwen2.5-coder:14b"
    assert row["qa_model"] == "qwen2.5:3b"
    assert row["po_backup_model"] == "llama3:8b"
    assert state.PRIMARY_MODELS["po"] == "qwen2.5:7b"
    assert state.PRIMARY_MODELS["dev"] == "qwen2.5-coder:32b"
    assert state.PRIMARY_MODELS["qa"] == "qwen2.5:3b"
    assert state.BACKUP_MODELS["dev"] == "qwen2.5-coder:7b"
    assert state.SKILLS_DIR == str(skills)

    ws = get_workflow_settings(pid)
    assert ws["requireToolApproval"] is True
    assert ws["toolApprovalTools"] == ["write_file", "run_command"]
    assert ws["commandAllowlist"] == ["pytest", "ruff check"]
    assert ws["discordBotAllowedUserIds"] == ["111", "222"]
    assert ws["mcpServers"][0]["name"] == "filesystem"
    assert ws["agentPrompts"]["Developer"]["system"] == "You are the project Developer."
    assert ws["agentTools"]["Developer"] == ["read_file", "write_file"]
    assert ws["executionProfile"] == "implementer"


def test_workflow_settings_api_updates_sidecar(tmp_path):
    workspace = tmp_path / "api-ws"
    workspace.mkdir()
    state.CURRENT_PROJECT_ID = "api-sidecar"
    state.PROJECT_NAME = "API Sidecar"
    state.WORKSPACE_DIR = str(workspace)
    state.SHARED_BOARD = {k: [] for k in DEFAULT_BOARD}
    state.PRIMARY_MODELS = dict(DEFAULT_ROLE_MODELS)
    save_current_project_state(persist_board=False)

    client = TestClient(app)
    r = client.post(
        "/api/workflow/settings",
        json={
            "requireToolApproval": True,
            "mcpServers": [{"name": "git", "command": "__allhands_missing_mcp__", "args": []}],
            "agentPrompts": {"Product Owner": {"system": "PO override", "stepInstructions": None}},
        },
    )
    assert r.status_code == 200
    data = read_project_file(str(workspace))
    assert data is not None
    assert data["workflow_settings"]["requireToolApproval"] is True
    assert data["workflow_settings"]["mcpServers"][0]["name"] == "git"
    assert data["workflow_settings"]["agentPrompts"]["Product Owner"]["system"] == "PO override"


def test_new_folder_does_not_inherit_previous_models(tmp_path):
    state.PRIMARY_MODELS = {
        "po": "gemma-4-q4km:26b",
        "dev": "gemma-4-q4km:26b",
        "cr": "gemma-4-q4km:26b",
        "qa": "gemma-4-q4km:26b",
    }
    folder = tmp_path / "fresh"
    folder.mkdir()
    open_workspace_folder(str(folder))
    data = read_project_file(str(folder))
    assert data is not None
    assert data["po_model"] == DEFAULT_ROLE_MODELS["po"]
    assert data["dev_model"] == DEFAULT_ROLE_MODELS["dev"]
    assert data["cr_model"] == DEFAULT_ROLE_MODELS["cr"]
    assert data["qa_model"] == DEFAULT_ROLE_MODELS["qa"]
    assert "gemma-4" not in data["po_model"]
    assert "gemma-4" not in data["dev_model"]
