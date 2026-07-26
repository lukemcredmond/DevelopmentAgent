"""Discord localhost control bot — dispatcher, allowlist, feature, model (no live Discord)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from backend import state
from backend.agents.registry import agent_dev
from backend.agents.task_context import init_new_task, normalize_task
from backend.bootstrap import initialize
from backend.services import discord_bot
from backend.services.board_lanes import FEATURES_LANE, normalize_board_lanes
from backend.services.board_service import move_board_stage
from backend.services.discord_bot import (
    CMD_ANSWER,
    CMD_APPROVE,
    CMD_BACKUP_DEV,
    CMD_CANCEL,
    CMD_CLAIM,
    CMD_EXTEND,
    CMD_FEATURE,
    CMD_MODEL,
    CMD_PAUSE,
    CMD_PENDING,
    CMD_RESUME,
    CMD_STATUS,
    dispatch_command,
    is_actor_allowed,
)
from backend.services.qdrant_auth import sanitize_workflow_settings_for_client
from backend.services.tool_approval import PendingToolApproval
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


def _allow() -> None:
    save_workflow_settings({"discordBotAllowedUserIds": ["111"]})


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
    _allow()
    state.SPRINT_CANCEL = False
    state.SPRINT_CANCEL_INTENT = None

    ok, body = dispatch_command(CMD_STATUS, actor_id="111")
    assert ok is True
    assert "Project:" in body
    assert "sprintCancel=False" in body

    ok2, body2 = dispatch_command(CMD_PAUSE, actor_id="111")
    assert ok2 is True
    assert "Paused" in body2
    assert "intent=paused" in body2
    assert state.SPRINT_CANCEL is True
    assert state.SPRINT_CANCEL_INTENT == "paused"


def test_cancel_vs_pause_intent():
    initialize()
    reset_workflow_settings()
    _allow()
    state.SPRINT_CANCEL = False
    ok, body = dispatch_command(CMD_CANCEL, actor_id="111")
    assert ok is True
    assert "Cancelled" in body
    assert state.SPRINT_CANCEL_INTENT == "cancelled"


def test_ah_feature_creates_draft_without_starting_sprint():
    initialize()
    reset_workflow_settings()
    _reset_board()
    state.SPRINT_CANCEL = False
    _allow()

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


def test_ah_answer_moves_needs_user_to_in_progress():
    initialize()
    reset_workflow_settings()
    _reset_board()
    _allow()
    task = init_new_task({"id": "T-NU-1", "title": "Need decision", "description": "Pick auth provider"})
    normalize_task(task)
    task["needsUserReason"] = "Which provider?"
    task["needsUserAction"] = "Reply with oauth or password"
    state.SHARED_BOARD.setdefault("Needs User", []).append(task)
    tid = str(task["id"])

    ok, body = dispatch_command(
        CMD_ANSWER,
        actor_id="111",
        options={"answer": "Use OAuth", "target": "dev", "task_id": tid},
    )
    assert ok is True
    assert tid in body
    assert "In Progress" in body
    assert any(t.get("id") == tid for t in state.SHARED_BOARD.get("In Progress", []) or [])
    assert not any(t.get("id") == tid for t in state.SHARED_BOARD.get("Needs User", []) or [])


def test_ah_approve_resolves_pending():
    initialize()
    reset_workflow_settings()
    _allow()
    state.PENDING_TOOL_APPROVALS.clear()
    approval = PendingToolApproval(
        id="appr-1",
        run_id="run-1",
        task_id="T-1",
        agent="Developer",
        agent_id="dev",
        tool_name="write_file",
        arguments={"path": "a.py"},
        timestamp="2026-01-01",
    )
    state.PENDING_TOOL_APPROVALS.append(approval)

    with patch(
        "backend.services.tool_execution_service.execute_deferred_approval",
        MagicMock(),
    ):
        ok, body = dispatch_command(
            CMD_APPROVE,
            actor_id="111",
            options={"decision": "approve", "approval_id": "appr-1"},
        )
    assert ok is True
    assert "approved" in body.lower()
    assert "pending=0" in body


def test_ah_pending_lists_needs_user():
    initialize()
    reset_workflow_settings()
    _reset_board()
    _allow()
    task = init_new_task({"id": "T-NU-2", "title": "Ask me", "description": "q"})
    normalize_task(task)
    task["needsUserReason"] = "Need API key"
    state.SHARED_BOARD.setdefault("Needs User", []).append(task)
    ok, body = dispatch_command(CMD_PENDING, actor_id="111")
    assert ok is True
    assert task["id"] in body
    assert "Need API key" in body


def test_resume_no_double_start(monkeypatch):
    initialize()
    reset_workflow_settings()
    _allow()
    state.SPRINT_CANCEL = True
    state.SPRINT_CANCEL_INTENT = "paused"
    calls = {"n": 0}

    def fake_start():
        calls["n"] += 1
        return True

    monkeypatch.setattr(discord_bot, "_start_auto_sprint_background", fake_start)
    monkeypatch.setattr(discord_bot, "is_auto_sprint_active", lambda: False)
    ok, body = dispatch_command(CMD_RESUME, actor_id="111")
    assert ok is True
    assert calls["n"] == 1
    assert state.SPRINT_CANCEL is False

    monkeypatch.setattr(discord_bot, "is_auto_sprint_active", lambda: True)
    ok2, body2 = dispatch_command(CMD_RESUME, actor_id="111")
    assert ok2 is True
    assert "already active" in body2.lower()
    assert calls["n"] == 1


def test_backup_dev_arms(monkeypatch):
    initialize()
    reset_workflow_settings()
    _reset_board()
    _allow()
    task = init_new_task({"id": "T-IP-1", "title": "Work", "description": "desc"})
    normalize_task(task)
    state.SHARED_BOARD.setdefault("In Progress", []).append(task)
    monkeypatch.setattr(
        "backend.services.backup_model.arm_backup_for_agent",
        lambda *a, **k: True,
    )
    monkeypatch.setattr(
        "backend.services.project_service.save_current_project_state",
        lambda: None,
    )
    ok, body = dispatch_command(CMD_BACKUP_DEV, actor_id="111")
    assert ok is True
    assert "armed" in body.lower()


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
    assert "/ah-answer" in readme
    assert "/ah-approve" in readme
    assert "/ah-claim" in readme
    assert "/ah-extend" in readme
    assert "discordBotEnabled" in readme


def test_ah_approve_card_moves_pending_approval():
    initialize()
    reset_workflow_settings()
    _reset_board()
    _allow()
    task = init_new_task({"id": "T-PA-1", "title": "Feature X", "description": "d"})
    normalize_task(task)
    state.SHARED_BOARD.setdefault("Pending Approval", []).append(task)
    ok, body = dispatch_command(
        CMD_APPROVE,
        actor_id="111",
        options={"kind": "card", "task_id": "T-PA-1"},
    )
    assert ok is True
    assert "Backlog" in body
    assert any(t.get("id") == "T-PA-1" for t in state.SHARED_BOARD.get("Backlog", []) or [])


def test_ah_claim_calls_service(monkeypatch):
    initialize()
    reset_workflow_settings()
    _allow()
    monkeypatch.setattr(
        "backend.agents.agent_run.get_active_run",
        lambda: None,
    )
    monkeypatch.setattr(
        "backend.agents.task_context.count_claimable_backlog_tasks",
        lambda: 2,
    )
    monkeypatch.setattr(
        "backend.services.board_service.claim_ready_backlog_tasks",
        lambda limit=3: ["T-1", "T-2"][:limit],
    )
    ok, body = dispatch_command(CMD_CLAIM, actor_id="111", options={"limit": 2})
    assert ok is True
    assert "T-1" in body


def test_ah_extend_calls_extend(monkeypatch):
    initialize()
    reset_workflow_settings()
    _reset_board()
    _allow()
    task = init_new_task({"id": "T-EX-1", "title": "Work", "description": "d"})
    normalize_task(task)
    state.SHARED_BOARD.setdefault("In Progress", []).append(task)
    monkeypatch.setattr(
        "backend.services.prompt_retry.extend_agent_step",
        lambda *a, **k: {"ok": True},
    )
    ok, body = dispatch_command(CMD_EXTEND, actor_id="111", options={"extra": 4})
    assert ok is True
    assert "Extended" in body
