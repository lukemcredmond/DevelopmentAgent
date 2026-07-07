import subprocess
from typing import Any, Dict, List, Optional

import requests
from fastapi import APIRouter, Query

from backend import state
from backend.services.llm_debug_log import clear_llm_log, get_llm_logs
from backend.services.model_timeline import build_model_timeline
from backend.services.qdrant_auth import qdrant_connection_settings, qdrant_request_headers
from backend.services.system_capacity import get_model_recommendations, probe_system_capacity

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


@router.get("/api/llm-logs/timeline")
def get_llm_timeline(
    taskId: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
):
    with state.STATE_LOCK:
        return build_model_timeline(task_id=taskId, limit=limit)


@router.get("/api/ollama/qdrant-health")
def qdrant_health(
    url: str | None = None,
    apiKey: str | None = None,
):
    q_url, stored_key = qdrant_connection_settings()
    target = (url or q_url).rstrip("/")
    key = (apiKey or stored_key or "").strip() or None
    headers = qdrant_request_headers(key)
    try:
        response = requests.get(f"{target}/collections", headers=headers, timeout=5)
        if response.status_code == 200:
            data = response.json()
            collections = [c.get("name") for c in data.get("result", {}).get("collections", [])]
            return {
                "ok": True,
                "url": target,
                "collections": collections,
                "apiKeyConfigured": bool(key),
            }
        body = response.text[:200]
        return {"ok": False, "url": target, "error": f"HTTP {response.status_code}: {body}"}
    except requests.RequestException as e:
        return {"ok": False, "url": target, "error": str(e)}


@router.get("/api/ollama/system-capacity")
def system_capacity():
    return probe_system_capacity()


@router.get("/api/ollama/model-recommendations")
def model_recommendations(ollamaUrl: str = "http://localhost:11434"):
    capacity = probe_system_capacity()
    installed: List[str] = []
    try:
        resp = requests.get(f"{ollamaUrl.rstrip('/')}/api/tags", timeout=5)
        if resp.status_code == 200:
            installed = [m.get("name") for m in resp.json().get("models", []) if m.get("name")]
    except requests.RequestException:
        pass
    return get_model_recommendations(capacity, installed_models=installed)
