from backend import state


def save_current_project_state() -> None:
    from backend.agents.registry import agent_cr, agent_dev, agent_po, agent_qa
    from backend.services.board_snapshots import write_board_snapshot

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
        agent_po.model,
        agent_dev.model,
        agent_cr.model,
        agent_qa.model,
    )
    try:
        write_board_snapshot(
            state.CURRENT_PROJECT_ID,
            state.SHARED_BOARD,
            project_name=state.PROJECT_NAME,
        )
    except Exception:
        pass
