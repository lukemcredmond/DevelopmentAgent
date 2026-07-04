import os
import shutil

from fastapi import APIRouter, HTTPException

from backend import state
from backend.agents.registry import AGENT_LABELS, AGENT_MAP
from backend.api.helpers import build_state_response
from backend.api.schemas import SkillPayload
from backend.services.logs import add_system_log
from backend.services.project_service import save_current_project_state
from backend.services.skills import scan_skills_directory

router = APIRouter()


@router.get("/api/skills")
def list_skills():
    skills = scan_skills_directory()
    return {
        "skillsDir": state.SKILLS_DIR,
        "workspaceDir": state.WORKSPACE_DIR,
        "skills": skills,
        "count": len(skills),
    }


@router.post("/api/assign-skill")
def assign_skill_to_agent(payload: SkillPayload):
    with state.STATE_LOCK:
        if payload.agent not in AGENT_MAP:
            raise HTTPException(status_code=400, detail="Invalid agent")
        agent = AGENT_MAP[payload.agent]

        skill_rel = payload.skillFile.replace("\\", "/").lstrip("/")
        src_path = os.path.normpath(os.path.join(state.SKILLS_DIR, skill_rel))
        skills_root = os.path.realpath(state.SKILLS_DIR)
        if not os.path.realpath(src_path).startswith(skills_root + os.sep) and os.path.realpath(src_path) != skills_root:
            raise HTTPException(status_code=400, detail="Skill path escapes the global skills directory.")
        if not os.path.exists(src_path):
            raise HTTPException(status_code=404, detail=f"Skill file '{skill_rel}' not found in global skills dir: {state.SKILLS_DIR}")

        dest_rel = os.path.join("skills", skill_rel.replace("/", os.sep))
        dest_path = os.path.join(state.WORKSPACE_DIR, dest_rel)
        os.makedirs(os.path.dirname(dest_path), exist_ok=True)

        try:
            shutil.copy2(src_path, dest_path)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to copy file asset to workspace: {str(e)}") from e

        if skill_rel not in agent.assigned_skills:
            agent.assigned_skills.append(skill_rel)

        vfs_key = dest_rel.replace("\\", "/")
        try:
            with open(dest_path, "r", encoding="utf-8") as f:
                state.VIRTUAL_FILESYSTEM[vfs_key] = f.read()
        except Exception:
            pass

        save_current_project_state()
        agent_label = AGENT_LABELS.get(payload.agent, payload.agent)
        add_system_log(
            agent_label,
            "success",
            f"Copied skill '{skill_rel}' into workspace at '{vfs_key}' and assigned to {agent_label} agent.",
        )
    return build_state_response()


@router.post("/api/remove-skill")
def remove_skill_from_agent(payload: SkillPayload):
    from backend.agents.registry import agent_cr, agent_dev, agent_po, agent_qa

    with state.STATE_LOCK:
        if payload.agent not in AGENT_MAP:
            raise HTTPException(status_code=400, detail="Invalid agent")
        agent = AGENT_MAP[payload.agent]

        if payload.skillFile in agent.assigned_skills:
            agent.assigned_skills.remove(payload.skillFile)

        skill_rel = payload.skillFile.replace("\\", "/").lstrip("/")
        all_assigned = agent_po.assigned_skills + agent_dev.assigned_skills + agent_cr.assigned_skills + agent_qa.assigned_skills
        if skill_rel not in all_assigned:
            dest_path = os.path.join(state.WORKSPACE_DIR, "skills", skill_rel.replace("/", os.sep))
            if os.path.exists(dest_path):
                try:
                    os.remove(dest_path)
                except Exception:
                    pass
            state.VIRTUAL_FILESYSTEM.pop(os.path.join("skills", skill_rel).replace("\\", "/"), None)

        save_current_project_state()
        add_system_log(
            payload.agent.upper() + " Agent",
            "info",
            f"Removed skill '{skill_rel}' from active agent system context.",
        )
    return build_state_response()
