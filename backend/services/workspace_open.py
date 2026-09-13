"""Open a workspace folder and hydrate identity + board from disk."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any, Dict, Optional

from backend.config import DEFAULT_BOARD
from backend.services.project_file import (
    PROJECT_FILE_NAME,
    read_project_file,
    restore_project_from_file,
    write_current_project_file,
)
from backend.services.recent_workspaces import touch_recent
from backend.storage.project_storage import count_board_tasks


def _folder_display_name(workspace_dir: str) -> str:
    return Path(workspace_dir).expanduser().resolve().name or "Project"


def ensure_project_sidecar(workspace_dir: str) -> str:
    """Create allhands.project.json if missing; return project id."""
    from backend import state
    from backend.services.project_file import (
        build_project_file_payload,
        role_models_for_new_project,
        write_project_file,
    )
    from backend.services.workflow_settings import DEFAULT_WORKFLOW_SETTINGS

    root = Path(workspace_dir).expanduser()
    root.mkdir(parents=True, exist_ok=True)
    existing = read_project_file(str(root))
    if existing:
        return str(existing["id"])
    pid = str(uuid.uuid4())
    name = _folder_display_name(str(root))
    models = role_models_for_new_project()
    skills_dir = str(getattr(state, "SKILLS_DIR", "") or "")
    payload = build_project_file_payload(
        project_id=pid,
        name=name,
        brief="",
        original_brief="",
        workspace_dir=str(root),
        po_skills=[],
        dev_skills=[],
        cr_skills=[],
        qa_skills=[],
        po_model=models["po"],
        dev_model=models["dev"],
        cr_model=models["cr"],
        qa_model=models["qa"],
        workflow_settings=dict(DEFAULT_WORKFLOW_SETTINGS),
        skills_dir=skills_dir,
    )
    write_project_file(str(root), payload)
    state.storage.save_project(
        pid,
        name,
        "",
        str(root),
        dict(DEFAULT_BOARD),
        {},
        [],
        [],
        [],
        [],
        models["po"],
        models["dev"],
        models["cr"],
        models["qa"],
        "",
        "",
        "",
        "",
        persist_board=True,
        force_board=True,
    )
    return pid


def richest_snapshot_board(project_id: str) -> Optional[Dict[str, Any]]:
    from backend.services.board_snapshots import board_snapshots_dir

    best: Optional[Dict[str, Any]] = None
    best_count = 0
    directory = board_snapshots_dir(project_id)
    for path in directory.glob("board-*.json"):
        try:
            import json

            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        board = data.get("board_state")
        count = int(data.get("taskCount") or count_board_tasks(board))
        if count > best_count and isinstance(board, dict):
            best = board
            best_count = count
    return best


def hydrate_board_from_workspace(workspace_dir: str, project_id: str) -> None:
    """Fill an empty in-memory board from card docs, v1 sidecar, or snapshots."""
    from backend import state
    from backend.services.board_lanes import normalize_board_lanes
    from backend.agents.task_context import dedupe_board_tasks, normalize_board_tasks
    from backend.services.project_service import save_current_project_state

    live = count_board_tasks(state.SHARED_BOARD)
    if live > 0:
        return

    from backend.services.task_spec_import import import_cards_from_task_specs

    import_cards_from_task_specs(overwrite=False)
    if count_board_tasks(state.SHARED_BOARD) > 0:
        normalize_board_tasks()
        dedupe_board_tasks()
        save_current_project_state(force_board=True)
        return

    data = read_project_file(workspace_dir) or {}
    v1 = data.get("board_state")
    if isinstance(v1, dict) and count_board_tasks(v1) > 0:
        state.SHARED_BOARD = normalize_board_lanes(v1)
        normalize_board_tasks()
        dedupe_board_tasks()
        save_current_project_state(force_board=True)
        return

    snap = richest_snapshot_board(project_id)
    if isinstance(snap, dict) and count_board_tasks(snap) > 0:
        state.SHARED_BOARD = normalize_board_lanes(snap)
        normalize_board_tasks()
        dedupe_board_tasks()
        save_current_project_state(force_board=True)
        return


def open_workspace_folder(workspace_dir: str) -> str:
    """Restore sidecar (creating if needed), load into state, hydrate cards, record recents."""
    from backend import state
    from backend.bootstrap import load_project_into_state

    workspace = str(Path(workspace_dir).expanduser())
    with state.STATE_LOCK:
        data = read_project_file(workspace)
        if data:
            project_id = restore_project_from_file(workspace)
        else:
            project_id = ensure_project_sidecar(workspace)
        if not load_project_into_state(project_id):
            raise RuntimeError("Failed to load workspace project")
        state.WORKSPACE_DIR = workspace
        hydrate_board_from_workspace(workspace, project_id)
        write_current_project_file()
        touch_recent(project_id=project_id, name=state.PROJECT_NAME, workspace_dir=workspace)
        return project_id
