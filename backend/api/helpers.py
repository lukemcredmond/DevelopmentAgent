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
from backend.workspace.files import (
    derive_project_lint_command,
    list_workspace_file_paths,
    sync_virtual_filesystem_from_disk,
)


def build_state_response(*, include_files: bool = True) -> dict:
    normalize_board_tasks()
    from backend.services.board_lanes import FEATURES_LANE
    from backend.services.feature_service import build_feature_rollup, is_feature_task
    from backend.services.project_evidence import list_project_evidence

    for feat in state.SHARED_BOARD.get(FEATURES_LANE, []):
        if isinstance(feat, dict) and is_feature_task(feat) and feat.get("id"):
            feat["featureRollup"] = build_feature_rollup(str(feat["id"]))

    file_paths = list_workspace_file_paths()
    file_list = sync_virtual_filesystem_from_disk() if include_files else {}
    ws = get_workflow_settings()
    from backend.services.discord_bot import get_discord_bot_status
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
        "recommendedLintCommand": derive_project_lint_command(),
        "projectToolEvidence": list_project_evidence(),
        "logs": state.SYSTEM_LOGS,
        "availableSkills": scan_skills_directory(),
        "assignedSkills": {
            "po": agent_po.assigned_skills,
            "dev": agent_dev.assigned_skills,
            "cr": agent_cr.assigned_skills,
            "qa": agent_qa.assigned_skills,
        },
        "models": {
            "po": (getattr(state, "PRIMARY_MODELS", {}) or {}).get("po") or agent_po.model,
            "dev": (getattr(state, "PRIMARY_MODELS", {}) or {}).get("dev") or agent_dev.model,
            "cr": (getattr(state, "PRIMARY_MODELS", {}) or {}).get("cr") or agent_cr.model,
            "qa": (getattr(state, "PRIMARY_MODELS", {}) or {}).get("qa") or agent_qa.model,
        },
        "backupModels": {
            "po": (getattr(state, "BACKUP_MODELS", {}) or {}).get("po") or "",
            "dev": (getattr(state, "BACKUP_MODELS", {}) or {}).get("dev") or "",
            "cr": (getattr(state, "BACKUP_MODELS", {}) or {}).get("cr") or "",
            "qa": (getattr(state, "BACKUP_MODELS", {}) or {}).get("qa") or "",
        },
        "projectsList": state.storage.list_projects(),
        "sprintCancel": state.SPRINT_CANCEL,
        "sprintCancelIntent": getattr(state, "SPRINT_CANCEL_INTENT", None),
        "workflowSettings": sanitize_workflow_settings_for_client(ws),
        "discordBotStatus": get_discord_bot_status(),
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
    if state.LAST_STEP_PROGRESS is not None:
        response["lastStepProgress"] = state.LAST_STEP_PROGRESS
    from backend.services.sprint_context_sources import get_last_sprint_context_sources

    ctx_src = get_last_sprint_context_sources()
    if ctx_src is not None:
        response["lastSprintContextSources"] = ctx_src
    from backend.services.step_diagnostics import get_active_trace_summary

    active_diag = get_active_trace_summary()
    if active_diag is not None:
        response["activeStepDiagnostics"] = active_diag
    from backend.services.sprint_session import get_recovery_context

    recovery = get_recovery_context()
    if recovery is not None:
        response["recovery"] = recovery
    from backend.services.simulation_gate import get_pending_simulation_public

    pending_sim = get_pending_simulation_public()
    if pending_sim is not None:
        response["pendingSimulation"] = pending_sim
        response["sprintPausedForSimulation"] = True
    from backend.services.backlog_preflight import build_backlog_preflight

    response["backlogPreflight"] = build_backlog_preflight()
    return response
