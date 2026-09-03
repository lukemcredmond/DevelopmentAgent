"""Workspace project file — like a .NET .sln sitting in the project folder.

Mirrors the SQLite project row (plus workflow settings) so a deleted DB row can
be re-opened from disk.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

PROJECT_FILE_NAME = "allhands.project.json"
PROJECT_FILE_FORMAT = "allhands-project"
PROJECT_FILE_VERSION = 1

_SECRET_KEYS = (
    "llmApiKey",
    "qdrantApiKey",
    "discordBotToken",
    "phoneNotifyDiscordWebhookUrl",
)


def project_file_path(workspace_dir: str) -> Path:
    return Path(workspace_dir).expanduser() / PROJECT_FILE_NAME


def _redact_settings(settings: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(settings or {})
    for key in _SECRET_KEYS:
        if key in out and out[key]:
            out[key] = ""
    return out


def build_project_file_payload(
    *,
    project_id: str,
    name: str,
    brief: str,
    workspace_dir: str,
    board_state: Dict[str, Any],
    po_skills: list,
    dev_skills: list,
    cr_skills: list,
    qa_skills: list,
    po_model: str,
    dev_model: str,
    cr_model: str,
    qa_model: str,
    po_backup_model: str = "",
    dev_backup_model: str = "",
    cr_backup_model: str = "",
    qa_backup_model: str = "",
    plan_outline: str = "",
    workflow_settings: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    return {
        "format": PROJECT_FILE_FORMAT,
        "formatVersion": PROJECT_FILE_VERSION,
        "id": project_id,
        "name": name,
        "brief": brief or "",
        "workspace_dir": workspace_dir,
        "board_state": board_state or {},
        "po_skills": list(po_skills or []),
        "dev_skills": list(dev_skills or []),
        "cr_skills": list(cr_skills or []),
        "qa_skills": list(qa_skills or []),
        "po_model": po_model,
        "dev_model": dev_model,
        "cr_model": cr_model,
        "qa_model": qa_model,
        "po_backup_model": po_backup_model or "",
        "dev_backup_model": dev_backup_model or "",
        "cr_backup_model": cr_backup_model or "",
        "qa_backup_model": qa_backup_model or "",
        "plan_outline": plan_outline or "",
        "workflow_settings": _redact_settings(workflow_settings or {}),
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def write_project_file(workspace_dir: str, payload: Dict[str, Any]) -> Optional[Path]:
    if not workspace_dir or not payload.get("id"):
        return None
    root = Path(workspace_dir).expanduser()
    try:
        root.mkdir(parents=True, exist_ok=True)
        path = root / PROJECT_FILE_NAME
        serialized = json.dumps(payload, indent=2, default=str)
        if path.is_file():
            try:
                if path.read_text(encoding="utf-8") == serialized:
                    return path
            except OSError:
                pass
        path.write_text(serialized, encoding="utf-8")
        return path
    except OSError:
        return None


def read_project_file(workspace_dir: str) -> Optional[Dict[str, Any]]:
    path = project_file_path(workspace_dir)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or not str(data.get("id") or "").strip():
        return None
    return data


def write_current_project_file() -> Optional[Path]:
    from backend import state
    from backend.agents.registry import agent_cr, agent_dev, agent_po, agent_qa
    from backend.services.workflow_settings import get_workflow_settings
    from backend.storage.project_storage import count_board_tasks

    stored = state.storage.load_project(state.CURRENT_PROJECT_ID) or {}
    stored_board = stored.get("board_state") if isinstance(stored.get("board_state"), dict) else {}
    memory_board = state.SHARED_BOARD or {}
    board = (
        stored_board
        if count_board_tasks(stored_board) >= count_board_tasks(memory_board)
        else memory_board
    )
    primary = getattr(state, "PRIMARY_MODELS", {}) or {}
    backup = getattr(state, "BACKUP_MODELS", {}) or {}
    payload = build_project_file_payload(
        project_id=state.CURRENT_PROJECT_ID,
        name=state.PROJECT_NAME,
        brief=state.PROJECT_BRIEF,
        workspace_dir=state.WORKSPACE_DIR,
        board_state=board,
        po_skills=agent_po.assigned_skills,
        dev_skills=agent_dev.assigned_skills,
        cr_skills=agent_cr.assigned_skills,
        qa_skills=agent_qa.assigned_skills,
        po_model=primary.get("po") or agent_po.model,
        dev_model=primary.get("dev") or agent_dev.model,
        cr_model=primary.get("cr") or agent_cr.model,
        qa_model=primary.get("qa") or agent_qa.model,
        po_backup_model=backup.get("po") or "",
        dev_backup_model=backup.get("dev") or "",
        cr_backup_model=backup.get("cr") or "",
        qa_backup_model=backup.get("qa") or "",
        plan_outline=getattr(state, "PROJECT_PLAN_OUTLINE", "") or "",
        workflow_settings=get_workflow_settings(state.CURRENT_PROJECT_ID),
    )
    return write_project_file(state.WORKSPACE_DIR, payload)


def restore_project_from_file(workspace_dir: str) -> str:
    """Insert or refresh the SQLite row from allhands.project.json. Returns project id."""
    from backend import state
    from backend.config import DEFAULT_BOARD, DEFAULT_VIRTUAL_FS
    from backend.storage.project_storage import count_board_tasks

    data = read_project_file(workspace_dir)
    if not data:
        raise FileNotFoundError(f"No {PROJECT_FILE_NAME} in {workspace_dir}")
    pid = str(data["id"])
    board = data.get("board_state") if isinstance(data.get("board_state"), dict) else dict(DEFAULT_BOARD)
    existing = state.storage.load_project(pid)
    stored_count = count_board_tasks((existing or {}).get("board_state"))
    incoming_count = count_board_tasks(board)
    force = existing is None or incoming_count >= stored_count
    files = (existing or {}).get("files") or dict(DEFAULT_VIRTUAL_FS)
    state.storage.save_project(
        pid,
        str(data.get("name") or "Restored project"),
        str(data.get("brief") or ""),
        str(data.get("workspace_dir") or workspace_dir),
        board,
        files,
        list(data.get("po_skills") or []),
        list(data.get("dev_skills") or []),
        list(data.get("cr_skills") or []),
        list(data.get("qa_skills") or []),
        str(data.get("po_model") or "llama3:8b"),
        str(data.get("dev_model") or "qwen2.5-coder:14b"),
        str(data.get("cr_model") or "qwen2.5-coder:7b"),
        str(data.get("qa_model") or "qwen2.5-coder:7b"),
        str(data.get("po_backup_model") or ""),
        str(data.get("dev_backup_model") or ""),
        str(data.get("cr_backup_model") or ""),
        str(data.get("qa_backup_model") or ""),
        plan_outline=str(data.get("plan_outline") or ""),
        persist_board=True,
        force_board=force,
    )
    settings = data.get("workflow_settings")
    if isinstance(settings, dict) and settings:
        from backend.services.workflow_settings import save_workflow_settings

        save_workflow_settings(settings, project_id=pid)
    return pid
