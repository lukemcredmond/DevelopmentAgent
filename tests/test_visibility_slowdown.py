"""Visibility + slowdown pack markers."""

from __future__ import annotations

from pathlib import Path


def test_visibility_slowdown_markers():
    root = Path(__file__).resolve().parents[1]
    readme = (root / "README.md").read_text(encoding="utf-8")
    assert "Embeddings (`nomic-embed-text`" in readme or "nomic-embed-text" in readme
    assert "do **not** summarize" in readme or "do not** summarize" in readme or "do **not** summarize" in readme
    assert "No live token streaming" in readme
    assert "Max tool iterations reached" in readme
    assert "Guild ID" in readme

    panel = (root / "frontend" / "src" / "components" / "WorkflowPanel.tsx").read_text(
        encoding="utf-8"
    )
    assert "type `/ah`" in panel or "type <span" in panel

    chat = (root / "frontend" / "src" / "components" / "ChatPanel.tsx").read_text(
        encoding="utf-8"
    )
    assert "chat-waiting-status" in chat
    assert "CLIENT_CHAT_MESSAGE_CAP" in chat
    assert "activeRun" in chat

    mem = (root / "frontend" / "src" / "utils" / "boardMemory.ts").read_text(encoding="utf-8")
    assert "CLIENT_TASK_FILES_CAP" in mem

    hook = (root / "frontend" / "src" / "hooks" / "useAppState.ts").read_text(encoding="utf-8")
    assert "enqueueAgentRun" in hook

    ctx = (root / "backend" / "agents" / "task_context.py").read_text(encoding="utf-8")
    assert "task[\"files\"] = normalized_files[-80:]" in ctx or "normalized_files[-80:]" in ctx
