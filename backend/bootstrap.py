import os

from backend import state
from backend.agents.registry import agent_cr, agent_dev, agent_po, agent_qa
from backend.agents.task_context import normalize_board_tasks
from backend.services.logs import add_system_log
from backend.services.project_service import save_current_project_state
from backend.services.board_lanes import normalize_board_lanes
from backend.services.skills import scan_skills_directory


def load_project_into_state(project_id: str) -> bool:
    proj = state.storage.load_project(project_id)
    if not proj:
        return False

    state.CURRENT_PROJECT_ID = proj["id"]
    state.PROJECT_NAME = proj["name"]
    state.PROJECT_BRIEF = proj.get("brief") or ""
    state.WORKSPACE_DIR = proj["workspace_dir"]
    state.SHARED_BOARD = normalize_board_lanes(proj["board_state"])
    normalize_board_tasks()
    save_current_project_state()

    state.VIRTUAL_FILESYSTEM = proj["files"]

    agent_po.assigned_skills = proj["po_skills"]
    agent_dev.assigned_skills = proj["dev_skills"]
    agent_cr.assigned_skills = proj.get("cr_skills", [])
    agent_qa.assigned_skills = proj["qa_skills"]

    agent_po.model = proj["po_model"]
    agent_dev.model = proj["dev_model"]
    agent_cr.model = proj["cr_model"]
    agent_qa.model = proj["qa_model"]

    saved_logs = state.storage.load_project_logs(project_id)
    if saved_logs:
        state.SYSTEM_LOGS[:] = saved_logs

    state.storage.set_active_project_id(state.CURRENT_PROJECT_ID)
    from backend.services.tool_aliases import load_pending_tools_for_project

    load_pending_tools_for_project(state.CURRENT_PROJECT_ID)
    return True


def initialize() -> None:
    add_system_log("System", "info", "All Hands Multi-Agent Backend framework live.")

    os.makedirs(state.WORKSPACE_DIR, exist_ok=True)
    os.makedirs(state.SKILLS_DIR, exist_ok=True)
    scan_skills_directory()

    saved_projects = state.storage.list_projects()
    active_id = state.storage.get_active_project_id()

    if active_id and load_project_into_state(active_id):
        add_system_log("System", "info", f"Loaded active workspace project: '{state.PROJECT_NAME}'")
    elif saved_projects:
        load_project_into_state(saved_projects[0]["id"])
        add_system_log("System", "info", f"Loaded workspace project: '{state.PROJECT_NAME}'")
    else:
        save_current_project_state()
        state.storage.set_active_project_id(state.CURRENT_PROJECT_ID)

    print("=" * 70)
    print("      STARTING FASTAPI AGENT WEB INTERFACE")
    print("      Local dashboard url: http://127.0.0.1:6767")
    print("=" * 70)
