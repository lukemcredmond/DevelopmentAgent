import os
import shutil

from fastapi import APIRouter, HTTPException

from backend import state
from backend.agents.registry import AGENT_LABELS, AGENT_MAP
from backend.api.helpers import build_state_response
from backend.api.schemas import BulkSkillPayload, CombineSkillsPayload, SaveBuiltSkillPayload, SkillPayload
from backend.services.logs import add_system_log
from backend.services.project_service import save_current_project_state
from backend.services.skills import normalize_skill_rel, resolve_skill_read_path, workspace_skill_path

router = APIRouter()


def _assign_one_skill(agent_key: str, skill_file: str) -> bool:
    """Copy skill to workspace and assign to agent. Returns True if newly assigned."""
    agent = AGENT_MAP[agent_key]
    skill_rel = normalize_skill_rel(skill_file)
    src_path = resolve_skill_read_path(skill_rel)
    if not src_path:
        raise HTTPException(
            status_code=404,
            detail=f"Skill file '{skill_rel}' not found in workspace or global skills dir.",
        )

    dest_path = workspace_skill_path(skill_rel)
    os.makedirs(os.path.dirname(dest_path), exist_ok=True)

    try:
        if os.path.realpath(src_path) != os.path.realpath(dest_path):
            shutil.copy2(src_path, dest_path)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to copy file asset to workspace: {str(e)}") from e

    newly_assigned = skill_rel not in agent.assigned_skills
    if newly_assigned:
        agent.assigned_skills.append(skill_rel)

    vfs_key = os.path.join("skills", skill_rel).replace("\\", "/")
    try:
        with open(dest_path, "r", encoding="utf-8") as f:
            state.VIRTUAL_FILESYSTEM[vfs_key] = f.read()
    except Exception:
        pass

    return newly_assigned


@router.post("/api/skills/combine")
def combine_skills(payload: CombineSkillsPayload):
    from backend.services.skill_combiner import combine_skills_preview

    if payload.agent not in AGENT_MAP:
        raise HTTPException(status_code=400, detail="Invalid agent")
    try:
        result = combine_skills_preview(
            agent_key=payload.agent,
            skill_files=payload.skillFiles,
            output_name=payload.outputName,
            ollama_url=payload.ollamaUrl,
        )
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Skill merge failed: {e}") from e
    agent_label = AGENT_LABELS.get(payload.agent, payload.agent)
    add_system_log(
        agent_label,
        "info",
        f"Combined skill preview ready from {len(result.get('sources') or [])} source(s).",
    )
    return result


@router.post("/api/skills/save-built")
def save_built_skill_route(payload: SaveBuiltSkillPayload):
    from backend.services.skill_combiner import save_built_skill

    with state.STATE_LOCK:
        try:
            save_built_skill(skill_rel=payload.skillRel, markdown=payload.markdown)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        save_current_project_state()
    return build_state_response()


@router.get("/api/skills")
def list_skills():
    from backend.services.skills import scan_skills_directory

    skills = scan_skills_directory()
    return {
        "skillsDir": state.SKILLS_DIR,
        "workspaceDir": state.WORKSPACE_DIR,
        "skills": skills,
        "count": len(skills),
    }


@router.get("/api/skills/suggestions")
def skill_suggestions(agent: str, limit: int = 5):
    from backend.services.skill_suggestions import build_suggestions_response

    if agent not in AGENT_MAP:
        raise HTTPException(status_code=400, detail="Invalid agent")
    return build_suggestions_response(agent, limit=min(limit, 20))


@router.post("/api/assign-skill")
def assign_skill_to_agent(payload: SkillPayload):
    with state.STATE_LOCK:
        if payload.agent not in AGENT_MAP:
            raise HTTPException(status_code=400, detail="Invalid agent")
        newly = _assign_one_skill(payload.agent, payload.skillFile)
        save_current_project_state()
        if newly:
            agent_label = AGENT_LABELS.get(payload.agent, payload.agent)
            add_system_log(
                agent_label,
                "success",
                f"Assigned skill '{payload.skillFile}' to {agent_label} agent.",
            )
    return build_state_response()


@router.post("/api/assign-skills")
def assign_skills_to_agent(payload: BulkSkillPayload):
    with state.STATE_LOCK:
        if payload.agent not in AGENT_MAP:
            raise HTTPException(status_code=400, detail="Invalid agent")
        if not payload.skillFiles:
            raise HTTPException(status_code=400, detail="No skills selected")
        assigned = 0
        for skill_file in payload.skillFiles:
            if _assign_one_skill(payload.agent, skill_file):
                assigned += 1
        save_current_project_state()
        agent_label = AGENT_LABELS.get(payload.agent, payload.agent)
        add_system_log(
            agent_label,
            "success",
            f"Assigned {assigned} skill(s) to {agent_label} agent.",
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

        skill_rel = normalize_skill_rel(payload.skillFile)
        all_assigned = agent_po.assigned_skills + agent_dev.assigned_skills + agent_cr.assigned_skills + agent_qa.assigned_skills
        if skill_rel not in all_assigned:
            dest_path = workspace_skill_path(skill_rel)
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
