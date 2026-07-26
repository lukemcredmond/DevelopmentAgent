from backend import state


def save_current_project_state() -> None:
    from backend.agents.registry import agent_cr, agent_dev, agent_po, agent_qa
    from backend.services.board_snapshots import write_board_snapshot

    # Always persist primary models — never a temporary backup swap on agent.model.
    primary = getattr(state, "PRIMARY_MODELS", {}) or {}
    backup = getattr(state, "BACKUP_MODELS", {}) or {}

    state.storage.save_project(
        state.CURRENT_PROJECT_ID,
        state.PROJECT_NAME,
        state.PROJECT_BRIEF,
        state.WORKSPACE_DIR,
        state.SHARED_BOARD,
        state.VIRTUAL_FILESYSTEM,
        agent_po.assigned_skills,
        agent_dev.assigned_skills,
        agent_cr.assigned_skills,
        agent_qa.assigned_skills,
        primary.get("po") or agent_po.model,
        primary.get("dev") or agent_dev.model,
        primary.get("cr") or agent_cr.model,
        primary.get("qa") or agent_qa.model,
        backup.get("po") or "",
        backup.get("dev") or "",
        backup.get("cr") or "",
        backup.get("qa") or "",
    )
    try:
        write_board_snapshot(
            state.CURRENT_PROJECT_ID,
            state.SHARED_BOARD,
            project_name=state.PROJECT_NAME,
        )
    except Exception:
        pass
