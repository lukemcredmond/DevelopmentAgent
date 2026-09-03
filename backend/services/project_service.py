from typing import Optional

from backend import state


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
    )
    sidecar_board = board
    if not wrote_board:
        stored = state.storage.load_project(pid) or {}
        stored_board = stored.get("board_state")
        if isinstance(stored_board, dict):
            sidecar_board = stored_board
    try:
        from backend.services.project_file import build_project_file_payload, write_project_file
        from backend.services.workflow_settings import get_workflow_settings

        write_project_file(
            workspace,
            build_project_file_payload(
                project_id=pid,
                name=name,
                brief=brief,
                workspace_dir=workspace,
                board_state=sidecar_board,
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
            ),
        )
    except Exception:
        pass
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
