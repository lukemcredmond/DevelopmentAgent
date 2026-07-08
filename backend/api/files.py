from fastapi import APIRouter, HTTPException

from backend import state
from backend.api.schemas import SaveFilePayload, SearchFilesPayload, ReindexPayload
from backend.workspace.files import get_file_tree, read_workspace_file, save_file_with_revision, search_files
from backend.storage.code_index import CodeIndexEngine

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


@router.get("/api/files/revisions")
def list_file_revisions(path: str, limit: int = 20):
    with state.STATE_LOCK:
        revisions = state.storage.get_file_revisions(state.CURRENT_PROJECT_ID, path, limit=limit)
        return {"path": path, "revisions": revisions}


@router.get("/api/search/semantic")
def semantic_search(q: str, limit: int = 8):
    with state.STATE_LOCK:
        engine = CodeIndexEngine()
        return {"query": q, "results": engine.search(q, limit=limit)}


@router.post("/api/search/reindex")
def reindex_codebase(payload: ReindexPayload | None = None):
    ollama_url = (payload.ollama_url if payload else None) or "http://localhost:11434"
    with state.STATE_LOCK:
        engine = CodeIndexEngine(ollama_url=ollama_url)
        result = engine.index_workspace()
        if not result.get("ok"):
            raise HTTPException(status_code=503, detail=result.get("error", "Reindex failed"))
        from backend.services.graphify_service import run_graphify_update

        graph_result = run_graphify_update()
        if graph_result.get("ok"):
            result["graphify"] = graph_result
        elif not graph_result.get("skipped"):
            result["graphify"] = graph_result
        return result


@router.get("/api/search/index-status")
def search_index_status():
    with state.STATE_LOCK:
        from backend.services.graphify_service import graphify_status

        return {**CodeIndexEngine().index_status(), "graphify": graphify_status()}
