"""Outbound Discord phone notify — no inbound ports."""

from __future__ import annotations

from pathlib import Path

from backend.bootstrap import initialize
from backend.services import phone_notify
from backend.services.phone_notify import (
    clear_notify_dedup,
    is_allowed_discord_webhook_url,
    notify_event,
    send_test_notification,
)
from backend.services.qdrant_auth import sanitize_workflow_settings_for_client
from backend.services.workflow_settings import (
    DEFAULT_WORKFLOW_SETTINGS,
    reset_workflow_settings,
    save_workflow_settings,
)


VALID_HOOK = "https://discord.com/api/webhooks/123/abc-secret-token"


def test_default_phone_notify_off():
    assert DEFAULT_WORKFLOW_SETTINGS.get("phoneNotifyEnabled") is False
    assert DEFAULT_WORKFLOW_SETTINGS.get("phoneNotifyProvider") == "discord"


def test_webhook_url_validation():
    assert is_allowed_discord_webhook_url(VALID_HOOK)
    assert is_allowed_discord_webhook_url("https://discordapp.com/api/webhooks/1/x")
    assert not is_allowed_discord_webhook_url("http://discord.com/api/webhooks/1/x")
    assert not is_allowed_discord_webhook_url("https://evil.com/api/webhooks/1/x")
    assert not is_allowed_discord_webhook_url("https://discord.com/api/channels/1")


def test_disabled_skips_http(monkeypatch):
    initialize()
    reset_workflow_settings()
    calls: list = []

    def fake_post(url, body, timeout):
        calls.append(url)

    monkeypatch.setattr(phone_notify, "_http_post", fake_post)
    clear_notify_dedup()
    notify_event("needs_user", "t", "b", task_id="t1", sync=True)
    assert calls == []


def test_enabled_posts_and_dedups(monkeypatch):
    initialize()
    reset_workflow_settings()
    save_workflow_settings(
        {
            "phoneNotifyEnabled": True,
            "phoneNotifyDiscordWebhookUrl": VALID_HOOK,
            "phoneNotifyOnNeedsUser": True,
        }
    )
    calls: list = []

    def fake_post(url, body, timeout):
        calls.append((url, body.decode("utf-8")))

    monkeypatch.setattr(phone_notify, "_http_post", fake_post)
    clear_notify_dedup()
    notify_event("needs_user", "Needs your answer", "Please decide", task_id="card1", sync=True)
    notify_event("needs_user", "Needs your answer", "Please decide", task_id="card1", sync=True)
    assert len(calls) == 1
    assert calls[0][0] == VALID_HOOK
    assert "Needs your answer" in calls[0][1]
    assert VALID_HOOK not in str(calls[0][1])  # content is not the URL; URL is endpoint


def test_rejects_non_discord_url(monkeypatch):
    initialize()
    reset_workflow_settings()
    save_workflow_settings(
        {
            "phoneNotifyEnabled": True,
            "phoneNotifyDiscordWebhookUrl": "https://example.com/hook",
        }
    )
    calls: list = []
    monkeypatch.setattr(phone_notify, "_http_post", lambda *a, **k: calls.append(1))
    clear_notify_dedup()
    result = send_test_notification()
    assert result.get("ok") is False
    assert result.get("error") == "invalid_webhook_url"
    assert calls == []


def test_sanitize_strips_webhook_url():
    out = sanitize_workflow_settings_for_client(
        {**DEFAULT_WORKFLOW_SETTINGS, "phoneNotifyDiscordWebhookUrl": VALID_HOOK}
    )
    assert "phoneNotifyDiscordWebhookUrl" not in out
    assert out.get("phoneNotifyDiscordWebhookConfigured") is True


def test_webhook_not_in_log_message(monkeypatch, capsys):
    initialize()
    reset_workflow_settings()
    save_workflow_settings(
        {
            "phoneNotifyEnabled": True,
            "phoneNotifyDiscordWebhookUrl": VALID_HOOK,
        }
    )
    logged: list[str] = []

    def fake_log(agent, level, msg):
        logged.append(msg)

    monkeypatch.setattr(phone_notify, "add_system_log", fake_log)
    monkeypatch.setattr(phone_notify, "_http_post", lambda *a, **k: None)
    clear_notify_dedup()
    notify_event("sprint_end", "Sprint completed", "steps=3", task_id="s1", sync=True)
    joined = " | ".join(logged)
    assert VALID_HOOK not in joined
    assert "abc-secret-token" not in joined


def test_ui_phone_notify_markers():
    root = Path(__file__).resolve().parents[1]
    panel = (root / "frontend" / "src" / "components" / "WorkflowPanel.tsx").read_text(
        encoding="utf-8"
    )
    assert "Phone alerts (outbound)" in panel
    assert "Enable Discord phone alerts" in panel
    assert "Send test" in panel
    readme = (root / "README.md").read_text(encoding="utf-8")
    assert "phoneNotifyEnabled" in readme
    assert "Discord" in readme
    assert "/api/workflow/phone-notify/test" in readme
    assert "phoneNotifyOnBoardStatus" in panel
    assert "Board status after each sprint step" in panel
