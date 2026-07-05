import io
import json
import os
import shutil
import uuid
import zipfile

from fastapi import APIRouter, HTTPException, UploadFile, File

from backend import state
from backend.agents.registry import agent_cr, agent_dev, agent_po, agent_qa
from backend.api.helpers import build_state_response
from backend.api.schemas import ConfigPayload, CreateProjectPayload
from backend.bootstrap import load_project_into_state
from backend.config import DEFAULT_BOARD, DEFAULT_VIRTUAL_FS
from backend.services.logs import add_system_log
from backend.services.project_service import save_current_project_state

router = APIRouter()


@router.post("/api/projects/create")
def create_new_project(payload: CreateProjectPayload):
    with state.STATE_LOCK:
        state.CURRENT_PROJECT_ID = str(uuid.uuid4())
        state.PROJECT_NAME = payload.projectName
        state.PROJECT_BRIEF = ""
        state.WORKSPACE_DIR = payload.workspaceDir

        state.SHARED_BOARD = {k: list(v) for k, v in DEFAULT_BOARD.items()}
        state.VIRTUAL_FILESYSTEM = dict(DEFAULT_VIRTUAL_FS)

        os.makedirs(state.WORKSPACE_DIR, exist_ok=True)

        save_current_project_state()
        state.storage.set_active_project_id(state.CURRENT_PROJECT_ID)

        add_system_log("System", "success", f"Created and loaded new project: '{state.PROJECT_NAME}' at {state.WORKSPACE_DIR}")
    return build_state_response()


@router.post("/api/projects/load/{project_id}")
def load_existing_project(project_id: str):
    with state.STATE_LOCK:
        if not load_project_into_state(project_id):
            raise HTTPException(status_code=404, detail="Workspace project not located.")
        add_system_log("System", "success", f"Successfully loaded project workspace: '{state.PROJECT_NAME}'")
    return build_state_response()


@router.delete("/api/projects/{project_id}")
def delete_project(project_id: str):
    with state.STATE_LOCK:
        if project_id == state.CURRENT_PROJECT_ID:
            raise HTTPException(status_code=400, detail="Cannot delete the active project.")
        if not state.storage.delete_project(project_id):
            raise HTTPException(status_code=404, detail="Project not found.")
        add_system_log("System", "info", f"Deleted project {project_id}")
    return {"ok": True, "projectsList": state.storage.list_projects()}


@router.get("/api/projects/{project_id}/export")
def export_project_zip(project_id: str):
    proj = state.storage.load_project(project_id)
    if not proj:
        raise HTTPException(status_code=404, detail="Project not found.")

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        meta = {k: v for k, v in proj.items() if k != "files"}
        zf.writestr("project.json", json.dumps(meta, indent=2))
        for path, content in proj.get("files", {}).items():
            zf.writestr(f"files/{path}", content)
        logs = state.storage.load_project_logs(project_id)
        if logs:
            zf.writestr("logs.json", json.dumps(logs, indent=2))
        chat = state.storage.get_chat_messages(project_id, limit=1000)
        if chat:
            zf.writestr("chat.json", json.dumps(chat, indent=2))

    buffer.seek(0)
    from fastapi.responses import StreamingResponse

    return StreamingResponse(
        buffer,
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename=project-{project_id}.zip"},
    )


@router.post("/api/projects/import")
async def import_project_zip(file: UploadFile = File(...)):
    with state.STATE_LOCK:
        content = await file.read()
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as zf:
                if "project.json" not in zf.namelist():
                    raise HTTPException(status_code=400, detail="Invalid project zip: missing project.json")
                meta = json.loads(zf.read("project.json"))
                new_id = str(uuid.uuid4())
                files: dict = {}
                for name in zf.namelist():
                    if name.startswith("files/") and not name.endswith("/"):
                        rel = name[len("files/") :]
                        files[rel] = zf.read(name).decode("utf-8", errors="replace")

                state.storage.save_project(
                    new_id,
                    meta.get("name", "Imported Project"),
                    meta.get("brief", ""),
                    meta.get("workspace_dir", f"./workspace_{new_id[:8]}"),
                    meta.get("board_state", DEFAULT_BOARD),
                    files or dict(DEFAULT_VIRTUAL_FS),
                    meta.get("po_skills", []),
                    meta.get("dev_skills", []),
                    meta.get("cr_skills", []),
                    meta.get("qa_skills", []),
                    meta.get("po_model", "llama3:8b"),
                    meta.get("dev_model", "qwen2.5-coder:14b"),
                    meta.get("cr_model", "qwen2.5-coder:7b"),
                    meta.get("qa_model", "qwen2.5-coder:7b"),
                )
                if "logs.json" in zf.namelist():
                    logs = json.loads(zf.read("logs.json"))
                    state.storage.save_project_logs(new_id, logs)
                load_project_into_state(new_id)
                add_system_log("System", "success", f"Imported project '{state.PROJECT_NAME}'")
        except zipfile.BadZipFile as e:
            raise HTTPException(status_code=400, detail=f"Invalid zip file: {e}") from e
    return build_state_response()


@router.post("/api/config")
def update_config(payload: ConfigPayload):
    with state.STATE_LOCK:
        state.PROJECT_NAME = payload.projectName
        state.WORKSPACE_DIR = payload.workspaceDir
        state.SKILLS_DIR = payload.skillsDir

        agent_po.model = payload.poModel
        agent_dev.model = payload.devModel
        agent_cr.model = payload.crModel
        agent_qa.model = payload.qaModel

        os.makedirs(state.WORKSPACE_DIR, exist_ok=True)
        os.makedirs(state.SKILLS_DIR, exist_ok=True)
        state.storage.set_setting("skills_dir", payload.skillsDir)

        save_current_project_state()
        add_system_log(
            "System",
            "info",
            f"Configuration updated: Project '{state.PROJECT_NAME}', workspace: '{state.WORKSPACE_DIR}', "
            f"global skills: '{state.SKILLS_DIR}'. Models: PO({agent_po.model}), Dev({agent_dev.model}), "
            f"Reviewer({agent_cr.model}), QA({agent_qa.model})",
        )
    return build_state_response()


@router.post("/api/reset")
def reset_workspace():
    with state.STATE_LOCK:
        state.SHARED_BOARD = {k: list(v) for k, v in DEFAULT_BOARD.items()}
        state.VIRTUAL_FILESYSTEM = dict(DEFAULT_VIRTUAL_FS)
        state.SYSTEM_LOGS.clear()

        if os.path.exists(state.WORKSPACE_DIR):
            for root, _dirs, files_in_dir in os.walk(state.WORKSPACE_DIR):
                for file in files_in_dir:
                    file_path = os.path.join(root, file)
                    if os.path.isfile(file_path) and not file.startswith("."):
                        try:
                            os.remove(file_path)
                        except Exception:
                            pass

        save_current_project_state()
        add_system_log("System", "info", "Workspace state cleared. Backlog lanes and directory files cleaned successfully.")
    return build_state_response()
