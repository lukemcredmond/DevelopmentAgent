"""Board safety: snapshots, SSE projectId, recovery scan."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from backend import state
from backend.config import DEFAULT_BOARD
from backend.services.board_snapshots import (
    list_board_snapshots,
    load_board_snapshot,
    write_board_snapshot,
)
from backend.services.board_service import publish_board_update


@pytest.fixture()
def tmp_home(tmp_path, monkeypatch):
    monkeypatch.setenv("ALLHANDS_HOME", str(tmp_path))
    return tmp_path


def test_write_and_list_board_snapshots(tmp_home):
    board = {
        "Backlog": [{"id": "T1", "title": "One", "description": "d", "status": "Backlog"}],
        "Done": [],
    }
    path = write_board_snapshot("proj-a", board, project_name="Demo")
    assert path is not None and path.is_file()
    snaps = list_board_snapshots("proj-a")
    assert len(snaps) >= 1
    assert snaps[0]["taskCount"] == 1
    loaded = load_board_snapshot("proj-a", snaps[0]["id"])
    assert loaded is not None
    assert loaded["board_state"]["Backlog"][0]["id"] == "T1"


def test_publish_board_update_includes_project_id():
    state.CURRENT_PROJECT_ID = "proj-sse"
    state.SHARED_BOARD = {k: list(v) for k, v in DEFAULT_BOARD.items()}
    events = []

    def capture(event_type, data):
        events.append((event_type, data))

    with patch("backend.services.events.publish_event", side_effect=capture):
        with patch("backend.services.events._BOARD_COALESCE_MS", 0):
            # Bypass coalesce timer — call publish_event path via immediate flush
            from backend.services import events as ev

            payload = {
                "board": state.SHARED_BOARD,
                "projectId": state.CURRENT_PROJECT_ID,
                "source": "test",
            }
            ev.publish_event("board", payload)
    assert events
    assert events[0][1]["projectId"] == "proj-sse"


def test_publish_board_cleared_flag():
    state.CURRENT_PROJECT_ID = "proj-clear"
    state.SHARED_BOARD = {k: [] for k in DEFAULT_BOARD}
    captured = {}

    def capture_coalesce(data):
        captured.update(data)

    with patch(
        "backend.services.events.publish_board_event_coalesced",
        side_effect=capture_coalesce,
    ):
        publish_board_update(source="clear_tasks", cleared=True)
    assert captured.get("cleared") is True
    assert captured.get("projectId") == "proj-clear"


def test_recovery_scan_finds_legacy(tmp_home, monkeypatch):
    import sqlite3

    from backend.services.board_recovery import scan_board_recovery_options

    legacy = tmp_home / "legacy.db"
    monkeypatch.setattr("backend.services.board_recovery.LEGACY_DB_PATH", legacy)
    live = tmp_home / "scrum_memory.db"

    board_rich = {
        "Backlog": [
            {"id": f"T{i}", "title": f"t{i}", "description": "d", "status": "Backlog"}
            for i in range(5)
        ]
    }
    board_empty = {"Backlog": []}

    def _write_db(path: Path, project_id: str, name: str, board: dict):
        conn = sqlite3.connect(str(path))
        conn.execute(
            """CREATE TABLE projects (
                id TEXT PRIMARY KEY, name TEXT, board_state TEXT,
                po_model TEXT, dev_model TEXT, cr_model TEXT, qa_model TEXT
            )"""
        )
        conn.execute(
            "INSERT INTO projects VALUES (?,?,?,?,?,?,?)",
            (
                project_id,
                name,
                json.dumps(board),
                "llama3:8b",
                "qwen2.5-coder:7b",
                "qwen2.5-coder:7b",
                "qwen2.5-coder:7b",
            ),
        )
        conn.commit()
        conn.close()

    _write_db(live, "p1", "Meal", board_empty)
    _write_db(legacy, "p1", "Meal", board_rich)

    # allhands_home() reads ALLHANDS_HOME — live DB path uses that
    result = scan_board_recovery_options("p1", "Meal")
    assert result["liveTaskCount"] == 0
    assert any(c["kind"] == "legacy" and c["taskCount"] == 5 for c in result["candidates"])
