"""Guard board UPSERTs, isolate pytest from live ALLHANDS_HOME, recover orphans."""

from __future__ import annotations

import hashlib
from pathlib import Path

from fastapi.testclient import TestClient

from backend import state
from backend.config import DEFAULT_BOARD, DEFAULT_VIRTUAL_FS
from backend.storage.project_storage import ProjectStorage, count_board_tasks


def _card(task_id: str) -> dict:
    return {"id": task_id, "title": task_id, "description": "d", "status": "Backlog"}


def _board_with(*ids: str) -> dict:
    board = {k: list(v) for k, v in DEFAULT_BOARD.items()}
    board["Backlog"] = [_card(i) for i in ids]
    return board


def _save(
    store: ProjectStorage,
    proj_id: str,
    board: dict,
    *,
    persist_board: bool = True,
    force_board: bool = False,
    name: str = "Demo",
) -> bool:
    return store.save_project(
        proj_id,
        name,
        "",
        "./workspace",
        board,
        dict(DEFAULT_VIRTUAL_FS),
        [],
        [],
        [],
        [],
        "llama3:8b",
        "qwen2.5-coder:14b",
        "qwen2.5-coder:7b",
        "qwen2.5-coder:7b",
        persist_board=persist_board,
        force_board=force_board,
    )


def test_empty_board_does_not_overwrite_richer_row():
    store = state.storage
    rich = _board_with("T1", "T2", "T3")
    assert _save(store, "keep-cards", rich) is True
    empty = {k: [] for k in DEFAULT_BOARD}
    wrote = _save(store, "keep-cards", empty)
    assert wrote is False
    loaded = store.load_project("keep-cards")
    assert count_board_tasks(loaded["board_state"]) == 3


def test_metadata_save_skips_board_rewrite():
    store = state.storage
    rich = _board_with("T1", "T2")
    _save(store, "meta-only", rich)
    empty = {k: [] for k in DEFAULT_BOARD}
    wrote = _save(store, "meta-only", empty, persist_board=False)
    assert wrote is False
    loaded = store.load_project("meta-only")
    assert count_board_tasks(loaded["board_state"]) == 2
    assert loaded["name"] == "Demo"


def test_force_board_allows_empty_overwrite():
    store = state.storage
    _save(store, "force-empty", _board_with("T1"))
    empty = {k: [] for k in DEFAULT_BOARD}
    wrote = _save(store, "force-empty", empty, force_board=True)
    assert wrote is True
    loaded = store.load_project("force-empty")
    assert count_board_tasks(loaded["board_state"]) == 0


def test_inflight_persist_cannot_empty_previous_project():
    from backend.services.project_service import save_current_project_state

    old_id = "proj-old"
    new_id = "proj-new"
    _save(state.storage, old_id, _board_with("A", "B", "C", "D"))
    state.CURRENT_PROJECT_ID = new_id
    state.PROJECT_NAME = "New"
    state.SHARED_BOARD = {k: [] for k in DEFAULT_BOARD}
    save_current_project_state(project_id=old_id)
    loaded = state.storage.load_project(old_id)
    assert count_board_tasks(loaded["board_state"]) == 4


def test_pytest_does_not_touch_live_allhands_db():
    live = Path.home() / ".allhands" / "scrum_memory.db"
    before = None
    if live.is_file():
        raw = live.read_bytes()
        before = (live.stat().st_mtime_ns, live.stat().st_size, hashlib.sha256(raw).hexdigest())
    _save(state.storage, "tmp-only", _board_with("X"))
    from backend.bootstrap import initialize

    initialize()
    save_current_project_state = __import__(
        "backend.services.project_service", fromlist=["save_current_project_state"]
    ).save_current_project_state
    state.SHARED_BOARD = {k: [] for k in DEFAULT_BOARD}
    save_current_project_state()
    assert Path(state.storage.db_path).resolve() != live.resolve()
    if before:
        raw = live.read_bytes()
        after = (live.stat().st_mtime_ns, live.stat().st_size, hashlib.sha256(raw).hexdigest())
        assert after == before


def test_delete_rejects_active_and_last_project():
    from backend.main import app

    store = state.storage
    _save(store, "only-one", _board_with("T1"), name="Only")
    state.CURRENT_PROJECT_ID = "only-one"
    client = TestClient(app)
    denied_active = client.delete("/api/projects/only-one")
    assert denied_active.status_code == 400
    _save(store, "second", _board_with("T2"), name="Second")
    denied_active2 = client.delete("/api/projects/only-one")
    assert denied_active2.status_code == 400
    ok = client.delete("/api/projects/second")
    assert ok.status_code == 200
    ids = {p["id"] for p in ok.json()["projectsList"]}
    assert "second" not in ids
    assert "only-one" in ids


def test_orphan_snapshots_listed_and_restored():
    from backend.services.board_recovery import (
        list_orphan_snapshot_projects,
        recreate_project_from_orphan,
    )
    from backend.services.board_snapshots import write_board_snapshot

    orphan_id = "deleted-meal-planner"
    board = _board_with("T1", "T2", "T3")
    path = write_board_snapshot(orphan_id, board, project_name="Meal Planner", force=True)
    assert path is not None
    orphans = list_orphan_snapshot_projects()
    match = next((o for o in orphans if o["id"] == orphan_id), None)
    assert match is not None
    assert match["kind"] == "orphan_snapshot"
    assert match["taskCount"] == 3
    recreate_project_from_orphan(orphan_id, board)
    loaded = state.storage.load_project(orphan_id)
    assert loaded is not None
    assert loaded["name"] == "Meal Planner"
    assert count_board_tasks(loaded["board_state"]) == 3
    assert not any(o["id"] == orphan_id for o in list_orphan_snapshot_projects())
