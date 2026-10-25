import json
import time
from typing import Any, Dict, List, Optional

import requests
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response, StreamingResponse

from backend import state
from backend.api.schemas import (
    LlmModelBatchTestPayload,
    LlmModelTestPayload,
    ModelBenchmarkImportPayload,
    ModelBenchmarkPayload,
    ModelBenchmarkRestoreBackupPayload,
)
from backend.services.llm_debug_log import clear_llm_log, get_llm_logs
from backend.services.llm_provider import get_chat_provider
from backend.services.model_benchmark_backup import (
    list_benchmark_backups,
    load_backup_file,
)
from backend.services.model_benchmark_runner import (
    benchmark_blocked_reason,
    cancel_benchmark,
    export_benchmark_snapshot,
    get_benchmark_snapshot,
    import_benchmark_snapshot,
    is_benchmark_running,
    start_model_benchmark,
)
from backend.services.model_test_runner import (
    build_test_provider,
    build_test_slots,
    get_job_snapshot,
    probe_model,
    start_agent_model_tests,
)
from backend.services.model_timeline import build_model_timeline
from backend.services.ollama_service_log import read_service_log_snapshot, stream_service_logs
from backend.services.qdrant_auth import qdrant_connection_settings, qdrant_request_headers
from backend.services.system_capacity import get_model_recommendations, probe_system_capacity

router = APIRouter()


@router.get("/api/preflight")
def preflight():
    """Offline readiness: endpoint, models, capacity, vector store, outbound features."""
    from backend.services.preflight import run_preflight

    return run_preflight()


@router.get("/api/llm/capacity")
def llm_capacity():
    """What we know about the machine actually serving the models."""
    from backend.services.llm_capacity import resolve_inference_capacity
    from backend.services.agent_efficiency import single_model_mode_active

    capacity = resolve_inference_capacity()
    return {
        **capacity.to_dict(),
        "singleModelActive": single_model_mode_active(),
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


@router.post("/api/llm/test-model")
def test_llm_model(payload: LlmModelTestPayload):
    provider = build_test_provider(payload.url)
    started = time.perf_counter()
    health = provider.health()
    return probe_model(provider, health, payload.model.strip(), started=started)


@router.post("/api/llm/test-agent-models")
def test_agent_models(payload: LlmModelBatchTestPayload):
    """Start a background run over every configured primary/backup slot.

    Returns immediately so a cold model load cannot time out the request; poll
    the status route for per-model progress.
    """
    with state.STATE_LOCK:
        primary_models = dict(payload.models or state.PRIMARY_MODELS)
        backup_models = dict(payload.backupModels or state.BACKUP_MODELS)

    slots = build_test_slots(primary_models, backup_models)
    return start_agent_model_tests(slots, url=payload.url)


@router.get("/api/llm/test-agent-models/status")
def test_agent_models_status():
    return get_job_snapshot()


@router.post("/api/llm/benchmark-models")
def post_benchmark_models(payload: ModelBenchmarkPayload):
    block = benchmark_blocked_reason()
    if block:
        raise HTTPException(status_code=409, detail=block)
    agent = str(payload.agent or "dev").strip().lower()
    if agent not in ("po", "dev", "cr", "qa"):
        raise HTTPException(status_code=400, detail=f"Unknown agent: {agent}")
    result = start_model_benchmark(
        payload.models,
        url=payload.url,
        agent_id=agent,
        resume=bool(payload.resume),
    )
    if result.get("status") == "blocked":
        raise HTTPException(status_code=409, detail=str(result.get("error") or "blocked"))
    return result


@router.get("/api/llm/benchmark-models/status")
def get_benchmark_models_status():
    return get_benchmark_snapshot()


@router.get("/api/llm/dev-model-assessment")
def get_dev_model_assessment(model: Optional[str] = Query(None)):
    from backend.services.dev_model_benchmark_guard import dev_model_assessment_payload

    return dev_model_assessment_payload(model)


@router.post("/api/llm/benchmark-models/cancel")
def post_benchmark_models_cancel():
    return cancel_benchmark()


@router.get("/api/llm/benchmark-models/export")
def get_benchmark_models_export(url: Optional[str] = None):
    data = export_benchmark_snapshot(url)
    results = data.get("results") if isinstance(data.get("results"), list) else []
    if not results:
        raise HTTPException(status_code=404, detail="No benchmark data to export")
    body = json.dumps(data, indent=2, default=str)
    return Response(
        content=body,
        media_type="application/json",
        headers={"Content-Disposition": 'attachment; filename="benchmark-export.json"'},
    )


@router.post("/api/llm/benchmark-models/import")
def post_benchmark_models_import(payload: ModelBenchmarkImportPayload):
    if is_benchmark_running():
        raise HTTPException(status_code=409, detail="Benchmark is running")
    result = import_benchmark_snapshot(payload.snapshot, url=payload.url)
    if result.get("status") == "blocked":
        raise HTTPException(status_code=400, detail=str(result.get("error") or "import failed"))
    return result


@router.get("/api/llm/benchmark-models/backups")
def get_benchmark_models_backups(url: Optional[str] = None):
    from backend.services.model_benchmark_runner import resolve_benchmark_url

    storage_url = resolve_benchmark_url(url)
    return {"url": storage_url, "backups": list_benchmark_backups(storage_url)}


@router.post("/api/llm/benchmark-models/restore-backup")
def post_benchmark_models_restore_backup(payload: ModelBenchmarkRestoreBackupPayload):
    if is_benchmark_running():
        raise HTTPException(status_code=409, detail="Benchmark is running")
    snapshot = load_backup_file(payload.filename)
    if not snapshot:
        raise HTTPException(status_code=404, detail="Backup file not found")
    result = import_benchmark_snapshot(snapshot, url=payload.url)
    if result.get("status") == "blocked":
        raise HTTPException(status_code=400, detail=str(result.get("error") or "restore failed"))
    return result


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
