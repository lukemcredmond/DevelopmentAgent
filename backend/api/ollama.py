import requests
from fastapi import APIRouter, Query

from backend import state
from backend.services.llm_debug_log import clear_llm_log, get_llm_logs

router = APIRouter()


@router.get("/api/ollama/health")
def ollama_health(url: str = "http://localhost:11434"):
    try:
        response = requests.get(f"{url.rstrip('/')}/api/tags", timeout=5)
        if response.status_code == 200:
            data = response.json()
            models = [m.get("name") for m in data.get("models", [])]
            return {"ok": True, "url": url, "models": models}
        return {"ok": False, "url": url, "error": f"HTTP {response.status_code}"}
    except requests.RequestException as e:
        return {"ok": False, "url": url, "error": str(e)}


@router.get("/api/ollama/logs")
def get_ollama_logs(
    limit: int = Query(default=200, ge=1, le=500),
    agent: str | None = None,
    taskId: str | None = None,
):
    with state.STATE_LOCK:
        return {"entries": get_llm_logs(limit=limit, agent=agent, task_id=taskId)}


@router.post("/api/ollama/logs/clear")
def clear_ollama_logs():
    with state.STATE_LOCK:
        return clear_llm_log()


@router.get("/api/ollama/qdrant-health")
def qdrant_health(url: str = "http://localhost:6333"):
    try:
        response = requests.get(f"{url.rstrip('/')}/collections", timeout=5)
        if response.status_code == 200:
            data = response.json()
            collections = [c.get("name") for c in data.get("result", {}).get("collections", [])]
            return {"ok": True, "url": url, "collections": collections}
        return {"ok": False, "url": url, "error": f"HTTP {response.status_code}"}
    except requests.RequestException as e:
        return {"ok": False, "url": url, "error": str(e)}
