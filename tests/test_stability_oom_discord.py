"""Stability: Discord watchdog markers and client memory helpers (via README/UI)."""

from __future__ import annotations

from pathlib import Path


def test_discord_watchdog_and_disconnect_markers():
    root = Path(__file__).resolve().parents[1]
    src = (root / "backend" / "services" / "discord_bot.py").read_text(encoding="utf-8")
    assert "on_disconnect" in src
    assert "on_resumed" in src
    assert "_discord_watchdog_loop" in src
    assert "watchdog_restarting_dead_bot" in src
    assert "_WATCHDOG_INTERVAL_SEC" in src


def test_frontend_board_memory_cap_markers():
    root = Path(__file__).resolve().parents[1]
    mem = (root / "frontend" / "src" / "utils" / "boardMemory.ts").read_text(encoding="utf-8")
    assert "CLIENT_TRANSCRIPT_CAP" in mem
    assert "mergeTaskHistory" in mem
    hook = (root / "frontend" / "src" / "hooks" / "useAppState.ts").read_text(encoding="utf-8")
    assert "trimBoardHistory" in hook
    assert "enqueueToolEvent" in hook
    assert "enqueueSprintProgress" in hook
    panel = (root / "frontend" / "src" / "components" / "WorkflowPanel.tsx").read_text(
        encoding="utf-8"
    )
    assert "Token saved ≠ connected" in panel or "Token saved" in panel
    readme = (root / "README.md").read_text(encoding="utf-8")
    assert "UI freezes / OOM after hours" in readme
    assert "Discord configured but silent" in readme
