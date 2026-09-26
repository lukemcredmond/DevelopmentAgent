from typing import Optional
import logging
import os
import uuid
from pathlib import Path

from backend import state

logger = logging.getLogger(__name__)


def _resolve_source_skills_dir(source: dict, source_project_id: str) -> str:
    from backend.services.project_file import read_project_file

    workspace = str(source.get("workspace_dir") or "").strip()
    if workspace:
        sidecar = read_project_file(workspace)
        if isinstance(sidecar, dict):
            sd = str(sidecar.get("skills_dir") or "").strip()
            if sd:
                return sd
    if source_project_id == state.CURRENT_PROJECT_ID:
        return str(getattr(state, "SKILLS_DIR", "") or "").strip()
    return ""


def clone_project_for_experiment(
    source_project_id: str,
    *,
    name: str,
    workspace_dir: str,
    po_model: Optional[str] = None,
    dev_model: Optional[str] = None,
    cr_model: Optional[str] = None,
    qa_model: Optional[str] = None,
    po_backup_model: Optional[str] = None,
    dev_backup_model: Optional[str] = None,
    cr_backup_model: Optional[str] = None,
    qa_backup_model: Optional[str] = None,
) -> str:
    """Copy brief, skills, and workflow settings; fresh board, plan, and workspace folder."""
    from backend.bootstrap import load_project_into_state
    from backend.config import DEFAULT_BOARD, DEFAULT_VIRTUAL_FS
    from backend.services.logs import add_system_log
    from backend.services.workflow_settings import copy_workflow_settings_blob

    source_id = str(source_project_id or "").strip()
    if not source_id:
        raise ValueError("source project id required")

    workspace = str(workspace_dir or "").strip()
    if not workspace:
        raise ValueError("workspaceDir required")
    project_name = str(name or "").strip()
    if not project_name:
        raise ValueError("projectName required")

    with state.STATE_LOCK:
        if source_id == state.CURRENT_PROJECT_ID:
            save_current_project_state(project_id=source_id)

    source = state.storage.load_project(source_id)
    if not source:
        raise LookupError("Project not found")

    source_ws = str(source.get("workspace_dir") or "").strip()
    try:
        if source_ws and Path(source_ws).expanduser().resolve() == Path(workspace).expanduser().resolve():
            raise ValueError("Clone workspace must differ from the source project workspace")
    except OSError:
        if os.path.normpath(source_ws) == os.path.normpath(workspace):
            raise ValueError("Clone workspace must differ from the source project workspace")

    skills_dir = _resolve_source_skills_dir(source, source_id)
    new_id = str(uuid.uuid4())
    os.makedirs(workspace, exist_ok=True)

    board = {k: list(v) for k, v in DEFAULT_BOARD.items()}
    files = dict(DEFAULT_VIRTUAL_FS)

    def _model(field: Optional[str], key: str, default: str) -> str:
        if field is not None and str(field).strip():
            return str(field).strip()
        return str(source.get(key) or default)

    po_m = _model(po_model, "po_model", "llama3:8b")
    dev_m = _model(dev_model, "dev_model", "qwen2.5-coder:14b")
    cr_m = _model(cr_model, "cr_model", "qwen2.5-coder:7b")
    qa_m = _model(qa_model, "qa_model", "qwen2.5-coder:7b")

    def _backup(field: Optional[str], key: str) -> str:
        if field is not None:
            return str(field).strip()
        return str(source.get(key) or "")

    state.storage.save_project(
        new_id,
        project_name,
        str(source.get("brief") or ""),
        workspace,
        board,
        files,
        list(source.get("po_skills") or []),
        list(source.get("dev_skills") or []),
        list(source.get("cr_skills") or []),
        list(source.get("qa_skills") or []),
        po_m,
        dev_m,
        cr_m,
        qa_m,
        _backup(po_backup_model, "po_backup_model"),
        _backup(dev_backup_model, "dev_backup_model"),
        _backup(cr_backup_model, "cr_backup_model"),
        _backup(qa_backup_model, "qa_backup_model"),
        plan_outline="",
        persist_board=True,
        force_board=True,
        original_brief=str(source.get("original_brief") or ""),
    )
    copy_workflow_settings_blob(source_id, new_id)

    with state.STATE_LOCK:
        load_project_into_state(new_id)
        if skills_dir:
            state.SKILLS_DIR = skills_dir
            state.storage.set_setting("skills_dir", skills_dir)
        state.PROJECT_PLAN_OUTLINE = ""
        state.PROJECT_TOOL_EVIDENCE = []
        state.CURRENT_SPRINT_REPORT = None
        state.SYSTEM_LOGS.clear()
        save_current_project_state(project_id=new_id, force_board=True)
        state.storage.set_active_project_id(new_id)

    from backend.services.recent_workspaces import touch_recent

    touch_recent(project_id=new_id, name=project_name, workspace_dir=workspace)
    add_system_log(
        "System",
        "success",
        f"Cloned project '{project_name}' from {source_id[:8]}… — empty board/plan; adjust models and run Plan outline/backlog",
    )
    return new_id


def save_current_project_state(
    *,
    project_id: Optional[str] = None,
    persist_board: bool = True,
    force_board: bool = False,
) -> None:
    from backend.agents.registry import agent_cr, agent_dev, agent_po, agent_qa
    from backend.services.board_snapshots import write_board_snapshot

    with state.STATE_LOCK:
        pid = project_id or state.CURRENT_PROJECT_ID
        # Always persist primary models — never a temporary backup swap on agent.model.
        primary = dict(getattr(state, "PRIMARY_MODELS", {}) or {})
        backup = dict(getattr(state, "BACKUP_MODELS", {}) or {})
        name = state.PROJECT_NAME
        brief = state.PROJECT_BRIEF
        workspace = state.WORKSPACE_DIR
        board = {
            lane: list(tasks) if isinstance(tasks, list) else tasks
            for lane, tasks in (state.SHARED_BOARD or {}).items()
        }
        files = dict(state.VIRTUAL_FILESYSTEM or {})
        plan_outline = getattr(state, "PROJECT_PLAN_OUTLINE", "") or ""
        original_brief = getattr(state, "PROJECT_ORIGINAL_BRIEF", "") or ""
        po_skills = list(agent_po.assigned_skills)
        dev_skills = list(agent_dev.assigned_skills)
        cr_skills = list(agent_cr.assigned_skills)
        qa_skills = list(agent_qa.assigned_skills)
        po_model = primary.get("po") or agent_po.model
        dev_model = primary.get("dev") or agent_dev.model
        cr_model = primary.get("cr") or agent_cr.model
        qa_model = primary.get("qa") or agent_qa.model

    wrote_board = state.storage.save_project(
        pid,
        name,
        brief,
        workspace,
        board,
        files,
        po_skills,
        dev_skills,
        cr_skills,
        qa_skills,
        po_model,
        dev_model,
        cr_model,
        qa_model,
        backup.get("po") or "",
        backup.get("dev") or "",
        backup.get("cr") or "",
        backup.get("qa") or "",
        plan_outline=plan_outline,
        persist_board=persist_board,
        force_board=force_board,
        original_brief=original_brief,
    )
    try:
        from backend.services.project_file import build_project_file_payload, write_project_file
        from backend.services.workflow_settings import get_workflow_settings

        write_project_file(
            workspace,
            build_project_file_payload(
                project_id=pid,
                name=name,
                brief=brief,
                original_brief=original_brief,
                workspace_dir=workspace,
                po_skills=po_skills,
                dev_skills=dev_skills,
                cr_skills=cr_skills,
                qa_skills=qa_skills,
                po_model=po_model,
                dev_model=dev_model,
                cr_model=cr_model,
                qa_model=qa_model,
                po_backup_model=backup.get("po") or "",
                dev_backup_model=backup.get("dev") or "",
                cr_backup_model=backup.get("cr") or "",
                qa_backup_model=backup.get("qa") or "",
                plan_outline=plan_outline,
                workflow_settings=get_workflow_settings(pid),
                skills_dir=str(getattr(state, "SKILLS_DIR", "") or ""),
            ),
        )
    except Exception:
        logger.exception("Failed to write allhands.project.json for project %s", pid)
    if not persist_board or not wrote_board:
        return
    try:
        write_board_snapshot(
            pid,
            board,
            project_name=name,
            force=force_board,
        )
    except Exception:
        pass
