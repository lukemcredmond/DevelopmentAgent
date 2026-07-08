"""Project memory API — expose SemanticMemoryEngine to the UI."""

from fastapi import APIRouter, HTTPException

from backend import state
from backend.api.schemas import MemoryCreatePayload, MemoryUpdatePayload
from backend.storage.memory_engine import SemanticMemoryEngine, create_memory_engine

router = APIRouter()


def _engine(ollama_url: str = "http://localhost:11434") -> SemanticMemoryEngine:
    return create_memory_engine(ollama_url=ollama_url.rstrip("/"))


@router.get("/api/memory")
def list_memories(
    agent: str | None = None,
    category: str | None = None,
    q: str | None = None,
    dedupe: bool = True,
    limit: int = 50,
    ollamaUrl: str = "http://localhost:11434",
):
    with state.STATE_LOCK:
        engine = _engine(ollamaUrl)
        entries = engine.list_for_project(
            agent=agent,
            category=category,
            q=q,
            dedupe=dedupe,
            limit=min(limit, 200),
        )
    return {"entries": entries, "count": len(entries)}


@router.post("/api/memory")
def create_memory(payload: MemoryCreatePayload, ollamaUrl: str = "http://localhost:11434"):
    content = payload.content.strip()
    if not content:
        raise HTTPException(status_code=400, detail="content is required")
    with state.STATE_LOCK:
        engine = _engine(ollamaUrl)
        engine.save_project_note(content, payload.category or "user_note", project_id=state.CURRENT_PROJECT_ID)
        entries = engine.list_for_project(limit=1)
    return {"ok": True, "entry": entries[0] if entries else None}


@router.delete("/api/memory/{memory_id}")
def delete_memory(memory_id: str):
    with state.STATE_LOCK:
        engine = create_memory_engine()
        if not engine.delete(memory_id, project_id=state.CURRENT_PROJECT_ID):
            raise HTTPException(status_code=404, detail="Memory not found")
    return {"ok": True}


@router.patch("/api/memory/{memory_id}")
def update_memory(memory_id: str, payload: MemoryUpdatePayload, ollamaUrl: str = "http://localhost:11434"):
    content = payload.content.strip()
    if not content:
        raise HTTPException(status_code=400, detail="content is required")
    with state.STATE_LOCK:
        engine = _engine(ollamaUrl)
        if not engine.update(
            memory_id,
            content,
            category=payload.category,
            project_id=state.CURRENT_PROJECT_ID,
        ):
            raise HTTPException(status_code=404, detail="Memory not found")
        entries = engine.list_for_project(limit=200)
        updated = next((e for e in entries if e.get("id") == memory_id), None)
    return {"ok": True, "entry": updated}
