from backend import state
from backend.agents.agent_run import get_active_run
from backend.agents.registry import agent_cr, agent_dev, agent_po, agent_qa
from backend.agents.task_context import normalize_board_tasks
from backend.services.skills import scan_skills_directory
from backend.services.tool_approval import list_pending_approvals
from backend.services.workflow_settings import (
    build_workflow_notifications,
    get_active_lanes,
    get_last_sprint_summary,
    get_workflow_settings,
)
from backend.workspace.files import list_workspace_file_paths, sync_virtual_filesystem_from_disk


def build_state_response(*, include_files: bool = True) -> dict:
    normalize_board_tasks()
    file_paths = list_workspace_file_paths()
    file_list = sync_virtual_filesystem_from_disk() if include_files else {}
    ws = get_workflow_settings()
    from backend.services.qdrant_auth import sanitize_workflow_settings_for_client

    response: dict = {
        "projectId": state.CURRENT_PROJECT_ID,
        "projectName": state.PROJECT_NAME,
        "brief": state.PROJECT_BRIEF,
        "projectPlanOutline": state.PROJECT_PLAN_OUTLINE,
        "workspaceDir": state.WORKSPACE_DIR,
        "skillsDir": state.SKILLS_DIR,
        "board": state.SHARED_BOARD,
        "filePaths": file_paths,
        "files": file_list,
        "logs": state.SYSTEM_LOGS,
        "availableSkills": scan_skills_directory(),
        "assignedSkills": {
            "po": agent_po.assigned_skills,
            "dev": agent_dev.assigned_skills,
            "cr": agent_cr.assigned_skills,
            "qa": agent_qa.assigned_skills,
        },
        "models": {
            "po": agent_po.model,
            "dev": agent_dev.model,
            "cr": agent_cr.model,
            "qa": agent_qa.model,
        },
        "projectsList": state.storage.list_projects(),
        "sprintCancel": state.SPRINT_CANCEL,
        "workflowSettings": sanitize_workflow_settings_for_client(ws),
        "activeLanes": get_active_lanes(ws),
        "briefChangelog": state.storage.get_brief_changelog(state.CURRENT_PROJECT_ID, limit=50),
        "lastSprintSummary": get_last_sprint_summary(),
        "notifications": build_workflow_notifications(),
        "chatMessages": state.storage.get_chat_messages(state.CURRENT_PROJECT_ID, limit=100),
        "activeAgentRun": get_active_run().to_dict() if get_active_run() else None,
        "pendingToolApprovals": list_pending_approvals(),
    }
    if state.LAST_STEP_OUTCOME is not None:
        response["lastStepOutcome"] = state.LAST_STEP_OUTCOME
    if state.LAST_STEP_DIAGNOSTICS is not None:
        response["lastStepDiagnostics"] = state.LAST_STEP_DIAGNOSTICS
    return response
