from fastapi import APIRouter, HTTPException

from backend import state
from backend.api.schemas import SaveFilePayload, SearchFilesPayload
from backend.workspace.files import get_file_tree, read_workspace_file, save_file_with_revision, search_files

router = APIRouter()


@router.get("/api/files/tree")
def file_tree():
    with state.STATE_LOCK:
        return {"tree": get_file_tree()}


@router.post("/api/files/save")
def save_file(payload: SaveFilePayload):
    with state.STATE_LOCK:
        try:
            result = save_file_with_revision(payload.path, payload.content, author=payload.author)
            return result
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e


@router.get("/api/files/read")
def read_file(path: str):
    with state.STATE_LOCK:
        content = read_workspace_file(path)
        if content.startswith("Error:"):
            raise HTTPException(status_code=404, detail=content)
        return {"path": path, "content": content}


@router.get("/api/files/search")
def search_workspace_files_get(q: str, limit: int = 50):
    with state.STATE_LOCK:
        return {"results": search_files(q, limit)}


@router.post("/api/files/search")
def search_workspace_files(payload: SearchFilesPayload):
    with state.STATE_LOCK:
        return {"results": search_files(payload.query, payload.limit)}


@router.get("/api/files/diff")
def file_diff_by_path(path: str):
    with state.STATE_LOCK:
        rev = state.storage.get_latest_revision_for_path(state.CURRENT_PROJECT_ID, path)
        if not rev:
            content = read_workspace_file(path)
            if content.startswith("Error:"):
                raise HTTPException(status_code=404, detail=content)
            return {
                "path": path,
                "previous_content": "",
                "content": content,
                "revision_id": None,
            }
        return {
            "revision_id": rev["id"],
            "path": rev["path"],
            "previous_content": rev.get("previous_content") or "",
            "content": rev["content"],
            "author": rev.get("author"),
            "created_at": rev.get("created_at"),
        }


@router.get("/api/files/revisions/{revision_id}/diff")
def revision_diff(revision_id: str):
    with state.STATE_LOCK:
        rev = state.storage.get_file_revision(revision_id)
        if not rev:
            raise HTTPException(status_code=404, detail="Revision not found")
        return {
            "revision_id": revision_id,
            "path": rev["path"],
            "previous_content": rev.get("previous_content") or "",
            "content": rev["content"],
            "author": rev.get("author"),
            "created_at": rev.get("created_at"),
        }
