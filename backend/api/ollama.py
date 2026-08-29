import time
from typing import Any, Dict, List, Optional

import requests
from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse

from backend import state
from backend.api.schemas import LlmModelBatchTestPayload, LlmModelTestPayload
from backend.services.llm_debug_log import clear_llm_log, get_llm_logs
from backend.services.llm_provider import get_chat_provider
from backend.services.model_timeline import build_model_timeline
from backend.services.ollama_service_log import read_service_log_snapshot, stream_service_logs
from backend.services.qdrant_auth import qdrant_connection_settings, qdrant_request_headers
from backend.services.system_capacity import get_model_recommendations, probe_system_capacity

router = APIRouter()
AGENT_MODEL_LABELS = {
    "po": "Product Owner",
    "dev": "Developer",
    "cr": "Code Reviewer",
    "qa": "QA Tester",
}


@router.get("/api/ollama/health")
def ollama_health(url: Optional[str] = None):
    provider = get_chat_provider(override_url=url)
    result = provider.health()
    payload: Dict[str, Any] = {
        "ok": result.ok,
        "url": result.url,
        "models": result.models,
        "provider": result.provider,
    }
    if result.error:
        payload["error"] = result.error
    return payload


def _test_model_with_health(
    provider: Any, health: Any, model: str, *, started: Optional[float] = None
) -> Dict[str, Any]:
    started = started or time.perf_counter()
    if not health.ok:
        return {
            "ok": False,
            "provider": health.provider,
            "url": health.url,
            "model": model,
            "models": health.models,
            "latencyMs": int((time.perf_counter() - started) * 1000),
            "errorType": "connection",
            "error": health.error or "LLM server is unreachable",
        }
    if not model:
        return {
            "ok": False,
            "provider": health.provider,
            "url": health.url,
            "model": model,
            "models": health.models,
            "latencyMs": int((time.perf_counter() - started) * 1000),
            "errorType": "model",
            "error": "Enter or select a model name before testing",
        }
    if health.models and model not in health.models:
        return {
            "ok": False,
            "provider": health.provider,
            "url": health.url,
            "model": model,
            "models": health.models,
            "latencyMs": int((time.perf_counter() - started) * 1000),
            "errorType": "model",
            "error": f"Model '{model}' is not returned by the server model list",
        }
    try:
        result = provider.chat(
            model,
            [{"role": "user", "content": "Reply with OK."}],
            options={"temperature": 0, "num_predict": 4},
        )
        content = str(getattr(getattr(result, "message", None), "content", "") or "").strip()
        return {
            "ok": True,
            "provider": health.provider,
            "url": health.url,
            "model": model,
            "models": health.models,
            "latencyMs": int((time.perf_counter() - started) * 1000),
            "response": content[:200],
        }
    except Exception as exc:
        return {
            "ok": False,
            "provider": health.provider,
            "url": health.url,
            "model": model,
            "models": health.models,
            "latencyMs": int((time.perf_counter() - started) * 1000),
            "errorType": "generation",
            "error": str(exc)[:500],
        }


@router.post("/api/llm/test-model")
def test_llm_model(payload: LlmModelTestPayload):
    provider = get_chat_provider(override_url=payload.url)
    started = time.perf_counter()
    health = provider.health()
    return _test_model_with_health(provider, health, payload.model.strip(), started=started)


@router.post("/api/llm/test-agent-models")
def test_agent_models(payload: LlmModelBatchTestPayload):
    """Test every configured primary/backup slot without mutating live agents."""
    with state.STATE_LOCK:
        primary_models = dict(payload.models or state.PRIMARY_MODELS)
        backup_models = dict(payload.backupModels or state.BACKUP_MODELS)

    slots: List[Dict[str, str]] = []
    for agent_id, agent_label in AGENT_MODEL_LABELS.items():
        primary = str(primary_models.get(agent_id) or "").strip()
        backup = str(backup_models.get(agent_id) or "").strip()
        if primary:
            slots.append(
                {"agentId": agent_id, "agent": agent_label, "slot": "primary", "model": primary}
            )
        if backup and backup != primary:
            slots.append(
                {"agentId": agent_id, "agent": agent_label, "slot": "backup", "model": backup}
            )

    provider = get_chat_provider(override_url=payload.url)
    health = provider.health()
    model_results: Dict[str, Dict[str, Any]] = {}
    for slot in slots:
        model = slot["model"]
        if model not in model_results:
            model_results[model] = _test_model_with_health(provider, health, model)

    results = [{**slot, **model_results[slot["model"]]} for slot in slots]
    return {
        "ok": bool(results) and all(result["ok"] for result in results),
        "provider": health.provider,
        "url": health.url,
        "models": health.models,
        "results": results,
        "uniqueModelsTested": len(model_results),
    }


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


@router.get("/api/ollama/service-logs")
def get_ollama_service_logs(lines: int = Query(default=50, ge=1, le=500)):
    return read_service_log_snapshot(lines)


@router.get("/api/ollama/service-logs/stream")
def stream_ollama_service_logs(lines: int = Query(default=50, ge=1, le=500)):
    return StreamingResponse(
        stream_service_logs(lines),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/api/ollama/system-capacity")
def system_capacity():
    return probe_system_capacity()


@router.get("/api/ollama/model-recommendations")
def model_recommendations(ollamaUrl: Optional[str] = None):
    capacity = probe_system_capacity()
    installed: List[str] = []
    installed_ok = False
    tags_error: Optional[str] = None
    provider = get_chat_provider(override_url=ollamaUrl)
    health = provider.health()
    if health.ok:
        installed = list(health.models)
        installed_ok = True
    else:
        tags_error = health.error
    result = get_model_recommendations(capacity, installed_models=installed)
    result["installedOk"] = installed_ok
    result["installedModels"] = installed
    result["provider"] = health.provider
    if tags_error:
        result["installedError"] = tags_error
    return result
