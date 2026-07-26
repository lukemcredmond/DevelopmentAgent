"""Discord localhost control bot — dispatcher, allowlist, feature, model (no live Discord)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from backend import state
from backend.agents.registry import agent_dev
from backend.bootstrap import initialize
from backend.services import discord_bot
from backend.services.board_lanes import FEATURES_LANE, normalize_board_lanes
from backend.services.discord_bot import (
    CMD_FEATURE,
    CMD_MODEL,
    CMD_PAUSE,
    CMD_STATUS,
    dispatch_command,
    is_actor_allowed,
)
from backend.services.qdrant_auth import sanitize_workflow_settings_for_client
from backend.services.workflow_settings import (
    DEFAULT_WORKFLOW_SETTINGS,
    reset_workflow_settings,
    save_workflow_settings,
)


def _reset_board() -> None:
    state.SHARED_BOARD.clear()
    normalize_board_lanes(state.SHARED_BOARD)


def _allow_settings(user_id: str = "111", guild: str = "999") -> dict:
    return {
        **DEFAULT_WORKFLOW_SETTINGS,
        "discordBotAllowedUserIds": [user_id],
        "discordBotGuildId": guild,
        "discordModelPresetFast": "fast-model:7b",
        "discordModelPresetQuality": "quality-model:14b",
    }


def test_default_discord_bot_off():
    assert DEFAULT_WORKFLOW_SETTINGS.get("discordBotEnabled") is False
    assert DEFAULT_WORKFLOW_SETTINGS.get("discordBotAllowedUserIds") == []


def test_sanitize_strips_bot_token():
    out = sanitize_workflow_settings_for_client(
        {**DEFAULT_WORKFLOW_SETTINGS, "discordBotToken": "super-secret-token"}
    )
    assert "discordBotToken" not in out
    assert out.get("discordBotTokenConfigured") is True


def test_allowlist_rejects_unknown_user():
    ws = _allow_settings("111", "999")
    assert is_actor_allowed("111", "999", settings=ws) is True
    assert is_actor_allowed("222", "999", settings=ws) is False
    assert is_actor_allowed("111", "888", settings=ws) is False  # wrong guild
    assert is_actor_allowed("111", None, settings=ws) is True  # DM ok if allowlisted


def test_dispatch_rejects_non_allowlisted(monkeypatch):
    initialize()
    reset_workflow_settings()
    save_workflow_settings({"discordBotAllowedUserIds": ["111"]})
    logged: list[str] = []

    def fake_log(source, level, text):
        logged.append(text)

    monkeypatch.setattr(discord_bot, "add_system_log", fake_log)
    ok, msg = dispatch_command(CMD_STATUS, actor_id="999", guild_id=None)
    assert ok is False
    assert "Not authorized" in msg
    assert any("source=discord" in t and "ok=False" in t for t in logged)


def test_dispatch_status_and_pause():
    initialize()
    reset_workflow_settings()
    _reset_board()
    save_workflow_settings({"discordBotAllowedUserIds": ["111"]})
    state.SPRINT_CANCEL = False

    ok, body = dispatch_command(CMD_STATUS, actor_id="111")
    assert ok is True
    assert "Project:" in body
    assert "sprintCancel=False" in body

    ok2, body2 = dispatch_command(CMD_PAUSE, actor_id="111")
    assert ok2 is True
    assert "Paused" in body2
    assert state.SPRINT_CANCEL is True


def test_ah_feature_creates_draft_without_starting_sprint():
    initialize()
    reset_workflow_settings()
    _reset_board()
    state.SPRINT_CANCEL = False
    save_workflow_settings({"discordBotAllowedUserIds": ["111"]})

    with patch("backend.services.discord_bot._start_auto_sprint_background") as mock_sprint:
        ok, body = dispatch_command(
            CMD_FEATURE,
            actor_id="111",
            options={"title": "Phone auth", "description": "OTP login"},
        )
        mock_sprint.assert_not_called()

    assert ok is True
    assert "Sprint not started" in body
    assert "FEAT-" in body
    features = state.SHARED_BOARD.get(FEATURES_LANE) or []
    assert any(t.get("title") == "Phone auth" for t in features)
    # No sprint cancel flip / no auto start from feature alone
    assert state.SPRINT_CANCEL is False


def test_ah_model_fast_sets_dev_primary():
    initialize()
    reset_workflow_settings()
    save_workflow_settings(
        {
            "discordBotAllowedUserIds": ["111"],
            "discordModelPresetFast": "fast-model:7b",
            "discordModelPresetQuality": "quality-model:14b",
        }
    )
    agent_dev.model = "other:1b"
    state.PRIMARY_MODELS = {**(getattr(state, "PRIMARY_MODELS", {}) or {}), "dev": "other:1b"}

    ok, body = dispatch_command(CMD_MODEL, actor_id="111", options={"preset": "fast"})
    assert ok is True
    assert "fast-model:7b" in body
    assert agent_dev.model == "fast-model:7b"
    assert (state.PRIMARY_MODELS or {}).get("dev") == "fast-model:7b"


def test_ui_and_readme_markers():
    root = Path(__file__).resolve().parents[1]
    panel = (root / "frontend" / "src" / "components" / "WorkflowPanel.tsx").read_text(
        encoding="utf-8"
    )
    assert "Phone / Discord control" in panel
    assert "Discord control bot (optional, localhost)" in panel
    assert "Enable Discord control bot" in panel
    assert "discordModelPresetFast" in panel
    readme = (root / "README.md").read_text(encoding="utf-8")
    assert "Discord control bot (optional, localhost)" in readme
    assert "/ah-status" in readme
    assert "discordBotEnabled" in readme
