"""Board status Discord digest + notify hooks."""

from __future__ import annotations

from pathlib import Path

from backend.bootstrap import initialize
from backend.services import phone_notify
from backend.services.board_status_digest import build_board_status_digest, notify_board_status_after_step
from backend.services.phone_notify import clear_notify_dedup, notify_if_enabled
from backend.services.workflow_settings import (
    DEFAULT_WORKFLOW_SETTINGS,
    reset_workflow_settings,
    save_workflow_settings,
)


VALID_HOOK = "https://discord.com/api/webhooks/123/abc-secret-token"


def test_default_board_status_on():
    assert DEFAULT_WORKFLOW_SETTINGS.get("phoneNotifyOnBoardStatus") is True


def test_build_board_status_digest_counts():
    board = {
        "Features": [{"id": "F1", "title": "Feat"}],
        "Backlog": [{"id": "T1", "title": "A"}, {"id": "T2", "title": "B"}],
        "Refinement": [],
        "In Progress": [{"id": "T3", "title": "Doing"}],
        "Needs PO": [],
        "Needs User": [{"id": "T4", "title": "Ask"}],
        "QA": [],
        "Done": [{"id": "T5", "title": "Done"}],
        "Code Review": [{"id": "T6", "title": "CR"}],
    }
    text = build_board_status_digest(
        board=board,
        project_name="DemoApp",
        active_task={"id": "T3", "title": "Doing"},
        handler="dev",
        agent="Developer",
    )
    assert "Project: DemoApp" in text
    assert "Backlog: 2" in text
    assert "In Progress: 1" in text
    assert "Needs User: 1" in text
    assert "Code Review: 1" in text
    assert "Needs your input: 1 card(s)" in text
    assert "[T3] Doing" in text
    assert "Developer" in text
    assert len(text) <= 1800


def test_board_status_skipped_when_disabled(monkeypatch):
    initialize()
    reset_workflow_settings()
    save_workflow_settings(
        {
            "phoneNotifyEnabled": True,
            "phoneNotifyDiscordWebhookUrl": VALID_HOOK,
            "phoneNotifyOnBoardStatus": False,
        }
    )
    called: list = []

    def capture(*a, **k):
        called.append(1)

    monkeypatch.setattr(phone_notify, "notify_event", capture)
    clear_notify_dedup()
    notify_if_enabled("board_status", "Board status", "Lanes — Backlog: 0", task_id="board:abc")
    assert called == []


def test_board_status_dedup_suppresses_identical(monkeypatch):
    initialize()
    reset_workflow_settings()
    save_workflow_settings(
        {
            "phoneNotifyEnabled": True,
            "phoneNotifyDiscordWebhookUrl": VALID_HOOK,
            "phoneNotifyOnBoardStatus": True,
        }
    )
    calls: list = []

    def fake_post(url, body, timeout):
        calls.append(body.decode("utf-8"))

    monkeypatch.setattr(phone_notify, "_http_post", fake_post)
    clear_notify_dedup()
    body = "Project: X\nLanes — Backlog: 1"
    phone_notify.notify_event("board_status", "Board status", body, task_id="board:same", sync=True)
    phone_notify.notify_event("board_status", "Board status", body, task_id="board:same", sync=True)
    assert len(calls) == 1
    assert "Board status" in calls[0]


def test_board_status_rejects_non_discord(monkeypatch):
    initialize()
    reset_workflow_settings()
    save_workflow_settings(
        {
            "phoneNotifyEnabled": True,
            "phoneNotifyDiscordWebhookUrl": "https://example.com/hook",
            "phoneNotifyOnBoardStatus": True,
        }
    )
    calls: list = []
    monkeypatch.setattr(phone_notify, "_http_post", lambda *a, **k: calls.append(1))
    clear_notify_dedup()
    result = phone_notify._send_sync("board_status", "Board status", "hi", task_id="board:z")
    assert result.get("ok") is False
    assert result.get("error") == "invalid_webhook_url"
    assert calls == []


def test_notify_board_status_after_step_respects_toggle(monkeypatch):
    initialize()
    reset_workflow_settings()
    save_workflow_settings(
        {
            "phoneNotifyEnabled": True,
            "phoneNotifyDiscordWebhookUrl": VALID_HOOK,
            "phoneNotifyOnBoardStatus": False,
        }
    )
    called: list = []
    monkeypatch.setattr(phone_notify, "notify_event", lambda *a, **k: called.append(1))
    notify_board_status_after_step(handler="idle")
    assert called == []


def test_refinement_decompose_prompt_markers():
    root = Path(__file__).resolve().parents[1]
    src = (root / "backend" / "services" / "sprint_service.py").read_text(encoding="utf-8")
    assert "DECOMPOSE CHECK FIRST" in src
    assert "DECOMPOSE FIRST" in src
    assert "add_backlog_tasks" in src
    assert "add_subtasks" in src


def test_ui_board_status_checkbox():
    root = Path(__file__).resolve().parents[1]
    panel = (root / "frontend" / "src" / "components" / "WorkflowPanel.tsx").read_text(
        encoding="utf-8"
    )
    assert "phoneNotifyOnBoardStatus" in panel
    assert "Board status after each sprint step" in panel
    readme = (root / "README.md").read_text(encoding="utf-8")
    assert "phoneNotifyOnBoardStatus" in readme
