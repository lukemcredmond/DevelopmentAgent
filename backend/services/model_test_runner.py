"""Sequential connectivity tests for every configured agent primary/backup model.

Runs in a background thread so a cold load of a large model cannot stall an HTTP
request, and exposes partial results so callers can show live progress.
"""

from __future__ import annotations

import threading
import time
import uuid
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

# Tokens allowed for the probe reply. Reasoning models emit thinking tokens first,
# so a handful of tokens would leave the visible content empty.
PROBE_MAX_TOKENS = 32
MAX_HEALTH_TIMEOUT_SEC = 30.0
# The probe sends a few tokens and asks for a few back, so a small context keeps
# the KV cache tiny. Just-in-time loads otherwise reserve a full-size cache and
# can be refused by a memory guardrail on large models.
PROBE_CONTEXT_LENGTH = 4096

AGENT_MODEL_LABELS: Dict[str, str] = {
    "po": "Product Owner",
    "dev": "Developer",
    "cr": "Code Reviewer",
    "qa": "QA Tester",
}

_JOB_LOCK = threading.Lock()
_JOB: Optional[Dict[str, Any]] = None


def model_test_timeout_sec() -> float:
    from backend.services.workflow_settings import get_workflow_settings

    return float(get_workflow_settings().get("modelTestTimeoutSec") or 600)


def build_test_provider(url: Optional[str] = None):
    """Chat provider with the model-test budget applied to generation and health."""
    from backend.services.llm_provider import get_chat_provider

    timeout = model_test_timeout_sec()
    provider = get_chat_provider(override_url=url)
    provider.timeout_sec = timeout
    provider.health_timeout_sec = min(MAX_HEALTH_TIMEOUT_SEC, timeout)
    return provider


def build_test_slots(
    primary_models: Dict[str, str],
    backup_models: Dict[str, str],
) -> List[Dict[str, str]]:
    """Ordered primary/backup slots, skipping blank backups and backup==primary."""
    slots: List[Dict[str, str]] = []
    for agent_id, agent_label in AGENT_MODEL_LABELS.items():
        primary = str((primary_models or {}).get(agent_id) or "").strip()
        backup = str((backup_models or {}).get(agent_id) or "").strip()
        if primary:
            slots.append(
                {"agentId": agent_id, "agent": agent_label, "slot": "primary", "model": primary}
            )
        if backup and backup != primary:
            slots.append(
                {"agentId": agent_id, "agent": agent_label, "slot": "backup", "model": backup}
            )
    return slots


def _unload_others(provider: Any, model: str) -> str:
    """Free VRAM held by other models. Returns a note only when noteworthy."""
    unload = getattr(provider, "unload_loaded_except", None)
    if not callable(unload):
        return ""
    try:
        info = unload(model) or {}
    except Exception as exc:
        return f"unload failed ({type(exc).__name__})"
    if not isinstance(info, dict):
        return ""
    status = info.get("status")
    if status == "unloaded":
        return f"unloaded {', '.join(info.get('unloaded') or [])}"
    if status in ("unavailable", "error"):
        return f"unload {status}: {info.get('detail') or 'no detail'}"
    return ""


def _load_with_small_context(provider: Any, model: str) -> Dict[str, Any]:
    load = getattr(provider, "load_model_for_test", None)
    if not callable(load):
        return {"status": "unsupported"}
    try:
        return load(model, context_length=PROBE_CONTEXT_LENGTH) or {"status": "unsupported"}
    except Exception as exc:
        return {"status": "error", "error": f"{type(exc).__name__}: {exc}"}


def probe_model(
    provider: Any,
    health: Any,
    model: str,
    *,
    started: Optional[float] = None,
    allow_reprobe: bool = True,
) -> Dict[str, Any]:
    """Probe one model: connection, then model availability, then a tiny generation."""
    started = started if started is not None else time.perf_counter()

    def _elapsed() -> int:
        return int((time.perf_counter() - started) * 1000)

    notes: Dict[str, Any] = {}

    def _failure(error_type: str, error: str) -> Dict[str, Any]:
        return {
            "ok": False,
            "provider": health.provider,
            "url": health.url,
            "model": model,
            "models": health.models,
            "latencyMs": _elapsed(),
            "errorType": error_type,
            "error": error,
            **notes,
        }

    if not health.ok:
        return _failure("connection", health.error or "LLM server is unreachable")
    if not model:
        return _failure("model", "Enter or select a model name before testing")
    if health.models and model not in health.models:
        # The list may be stale, or the server may only advertise loaded models.
        if allow_reprobe:
            health = provider.health()
            if not health.ok:
                return _failure("connection", health.error or "LLM server is unreachable")
        if health.models and model not in health.models:
            return _failure("model", f"Model '{model}' is not returned by the server model list")

    unload_note = _unload_others(provider, model)
    if unload_note:
        notes["unloadStatus"] = unload_note

    load_info = _load_with_small_context(provider, model)
    if load_info.get("status") == "error":
        return _failure(
            "load", f"model load refused: {load_info.get('error') or 'unknown error'}"
        )
    if load_info.get("status") == "unavailable":
        notes["loadStatus"] = str(load_info.get("detail") or "explicit load unavailable")
    elif load_info.get("status") == "loaded":
        context = (load_info.get("config") or {}).get("context_length")
        if context:
            notes["contextLength"] = context

    try:
        result = provider.chat(
            model,
            [{"role": "user", "content": "Reply with OK."}],
            options={
                "temperature": 0,
                "num_predict": PROBE_MAX_TOKENS,
                "num_ctx": PROBE_CONTEXT_LENGTH,
            },
        )
        content = str(getattr(getattr(result, "message", None), "content", "") or "").strip()
        return {
            "ok": True,
            "provider": health.provider,
            "url": health.url,
            "model": model,
            "models": health.models,
            "latencyMs": _elapsed(),
            "response": content[:200],
            **notes,
        }
    except Exception as exc:
        return _failure("generation", str(exc)[:500])


def run_agent_model_tests(
    slots: List[Dict[str, str]],
    provider: Any,
    *,
    on_model_start: Optional[Callable[[str], None]] = None,
    on_result: Optional[Callable[[str, Dict[str, Any]], None]] = None,
) -> Dict[str, Any]:
    """Test each unique model once, sequentially, mapping results back to every slot.

    Sequential by design: loading several large models at once thrashes VRAM.
    """
    health = provider.health()
    model_results: Dict[str, Dict[str, Any]] = {}
    for slot in slots:
        model = slot["model"]
        if model in model_results:
            continue
        if on_model_start is not None:
            on_model_start(model)
        result = probe_model(provider, health, model)
        model_results[model] = result
        if on_result is not None:
            on_result(model, result)

    results = [{**slot, **model_results[slot["model"]]} for slot in slots]
    return {
        "ok": bool(results) and all(item["ok"] for item in results),
        "provider": health.provider,
        "url": health.url,
        "models": health.models,
        "results": results,
        "uniqueModelsTested": len(model_results),
    }


def _pending_rows(slots: List[Dict[str, str]]) -> List[Dict[str, Any]]:
    return [{**slot, "status": "pending"} for slot in slots]


def _snapshot_locked() -> Dict[str, Any]:
    if _JOB is None:
        return {
            "runId": None,
            "status": "idle",
            "total": 0,
            "completed": 0,
            "currentModel": None,
            "results": [],
            "uniqueModelsTested": 0,
        }
    job = dict(_JOB)
    job["results"] = [dict(row) for row in _JOB["results"]]
    job["elapsedMs"] = int((time.perf_counter() - _JOB["_startedMono"]) * 1000)
    job.pop("_startedMono", None)
    return job


def get_job_snapshot() -> Dict[str, Any]:
    with _JOB_LOCK:
        return _snapshot_locked()


def _mark_model_started(model: str) -> None:
    with _JOB_LOCK:
        if _JOB is None:
            return
        _JOB["currentModel"] = model
        for row in _JOB["results"]:
            if row["model"] == model and row["status"] == "pending":
                row["status"] = "testing"


def _mark_model_result(model: str, result: Dict[str, Any]) -> None:
    with _JOB_LOCK:
        if _JOB is None:
            return
        status = "passed" if result.get("ok") else "failed"
        for row in _JOB["results"]:
            if row["model"] != model:
                continue
            row.update(result)
            row["status"] = status
        _JOB["completed"] = sum(
            1 for row in _JOB["results"] if row["status"] in ("passed", "failed")
        )
        _JOB["uniqueModelsTested"] = len(
            {row["model"] for row in _JOB["results"] if row["status"] in ("passed", "failed")}
        )


def _finish_job(summary: Optional[Dict[str, Any]], error: Optional[str]) -> None:
    with _JOB_LOCK:
        if _JOB is None:
            return
        _JOB["status"] = "done"
        _JOB["currentModel"] = None
        if error:
            _JOB["error"] = error
            _JOB["ok"] = False
        elif summary is not None:
            _JOB["ok"] = summary.get("ok")
            _JOB["provider"] = summary.get("provider")
            _JOB["url"] = summary.get("url")


def start_agent_model_tests(
    slots: List[Dict[str, str]],
    *,
    url: Optional[str] = None,
) -> Dict[str, Any]:
    """Start a background run. A run already in flight is returned unchanged."""
    global _JOB

    with _JOB_LOCK:
        if _JOB is not None and _JOB["status"] == "running":
            return _snapshot_locked()
        _JOB = {
            "runId": uuid.uuid4().hex[:12],
            "status": "running",
            "startedAt": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "_startedMono": time.perf_counter(),
            "total": len(slots),
            "completed": 0,
            "currentModel": None,
            "results": _pending_rows(slots),
            "uniqueModelsTested": 0,
            "ok": None,
            "timeoutSec": int(model_test_timeout_sec()),
        }
        snapshot = _snapshot_locked()

    def _run() -> None:
        try:
            provider = build_test_provider(url)
            summary = run_agent_model_tests(
                slots,
                provider,
                on_model_start=_mark_model_started,
                on_result=_mark_model_result,
            )
            _finish_job(summary, None)
        except Exception as exc:
            _finish_job(None, f"{type(exc).__name__}: {exc}"[:500])

    if slots:
        threading.Thread(target=_run, name="model-test-runner", daemon=True).start()
    else:
        _finish_job({"ok": False, "provider": "", "url": ""}, "No models configured to test")

    return snapshot


def reset_job_state() -> None:
    """Test hook: drop any recorded run."""
    global _JOB

    with _JOB_LOCK:
        _JOB = None
