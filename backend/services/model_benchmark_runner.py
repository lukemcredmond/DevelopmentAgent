"""Sequential model benchmark: load each chat model, tool probes, then eval coding tasks.

Assign Developer only to models with a strong nativeToolCallRate in benchmark results.
Prose-only or THOUGHT-style chat models fail Dev steps (text_rejection_loop) even when fast.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from backend import state
from backend.services.llm_provider import _normalize_url

logger = logging.getLogger(__name__)

NON_CHAT_HINTS = ("embed", "bge-", "nomic-embed", "all-minilm", "gte-")
BENCHMARK_KEY_PREFIX = "model_benchmark_last:"

_JOB_LOCK = threading.Lock()
_JOB: Optional[Dict[str, Any]] = None
_CANCEL_REQUESTED = False


def score_tier(score: Optional[int]) -> str:
    if score is None:
        return "unknown"
    if score >= 85:
        return "good"
    if score >= 50:
        return "warn"
    return "bad"


def compute_tool_scores(summary: Dict[str, Any]) -> Tuple[int, float]:
    passed = int(summary.get("pass") or 0)
    failed = int(summary.get("fail") or 0)
    denom = passed + failed
    rate = (passed / denom) if denom else 0.0
    return round(100 * rate), rate


def compute_code_scores(eval_summary: Dict[str, Any]) -> Tuple[int, float, float]:
    pass_rate = float(eval_summary.get("passRate") or 0.0)
    recovery = float(eval_summary.get("toolRecoveryRate") or 0.0)
    native = max(0.0, min(1.0, 1.0 - recovery))
    code_score = round(100 * (0.7 * pass_rate + 0.3 * native))
    return code_score, pass_rate, native


def compute_overall_score(tool_score: int, code_score: int) -> int:
    return round(0.35 * tool_score + 0.65 * code_score)


def pick_best_model(rows: List[Dict[str, Any]]) -> Optional[str]:
    candidates = [
        r
        for r in rows
        if r.get("loadOk") and isinstance(r.get("overallScore"), int)
    ]
    if not candidates:
        return None

    def sort_key(r: Dict[str, Any]) -> Tuple[int, int, float]:
        return (
            -int(r.get("overallScore") or 0),
            -int(r.get("codeScore") or 0),
            float(r.get("wallClockSec") or 1e9),
        )

    candidates.sort(key=sort_key)
    return str(candidates[0].get("model") or "")


def filter_chat_models(models: List[str]) -> List[str]:
    out: List[str] = []
    for name in models:
        m = str(name or "").strip()
        if not m:
            continue
        lower = m.lower()
        if any(h in lower for h in NON_CHAT_HINTS):
            continue
        out.append(m)
    return out


def benchmark_storage_url(url: str) -> str:
    return _normalize_url(url) or "default"


def _storage_key(url: str) -> str:
    canon = benchmark_storage_url(url)
    return f"{BENCHMARK_KEY_PREFIX}{canon}"[:200]


def _ollama_legacy_url_alternates(canon_url: str) -> List[str]:
    if canon_url == "http://localhost:11434":
        return ["http://127.0.0.1:11434"]
    if canon_url == "http://127.0.0.1:11434":
        return ["http://localhost:11434"]
    return []


def resolve_benchmark_url(url: Optional[str] = None) -> str:
    if url:
        return benchmark_storage_url(url)
    try:
        from backend.services.llm_provider import chat_config

        base = str(chat_config().get("baseUrl") or "")
        if base:
            return benchmark_storage_url(base)
    except Exception:
        pass
    return benchmark_storage_url(str(getattr(state, "OLLAMA_URL", "") or ""))


def load_persisted_benchmark(url: str) -> Optional[Dict[str, Any]]:
    """Last finished or cancelled benchmark snapshot for this LLM URL."""
    return _load_persisted_with_fallback(url)


def is_skippable_done_row(row: Dict[str, Any]) -> bool:
    return (
        str(row.get("status") or "") == "done"
        and bool(row.get("loadOk"))
        and row.get("overallScore") is not None
    )


def _resume_counts(rows: List[Dict[str, Any]]) -> Tuple[int, int]:
    done = sum(1 for r in rows if is_skippable_done_row(r))
    remaining = sum(1 for r in rows if not is_skippable_done_row(r))
    return done, remaining


def can_resume_benchmark(url: Optional[str] = None) -> Dict[str, Any]:
    with _JOB_LOCK:
        if _JOB is not None and _JOB.get("status") == "running":
            return {
                "canResume": False,
                "doneCount": 0,
                "remainingCount": 0,
                "totalModels": 0,
                "bestModel": None,
                "lastStatus": "running",
            }
    ollama_url = resolve_benchmark_url(url)
    persisted = _load_persisted_with_fallback(ollama_url)
    if not persisted:
        return {
            "canResume": False,
            "doneCount": 0,
            "remainingCount": 0,
            "totalModels": 0,
            "bestModel": None,
            "lastStatus": None,
        }
    rows = [r for r in (persisted.get("results") or []) if isinstance(r, dict)]
    done_count, remaining_count = _resume_counts(rows)
    last_status = str(persisted.get("status") or "done")
    resumable_status = last_status in ("cancelled", "done") or (
        last_status == "running" and done_count > 0
    )
    can = done_count > 0 and remaining_count > 0 and resumable_status
    return {
        "canResume": can,
        "doneCount": done_count,
        "remainingCount": remaining_count,
        "totalModels": len(rows),
        "bestModel": persisted.get("bestModel") or pick_best_model(rows),
        "lastStatus": last_status,
    }


def merge_resume_benchmark(
    persisted: Dict[str, Any],
    installed_chat_models: List[str],
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Merge persisted rows with current installs; return (all rows, models to run)."""
    by_name: Dict[str, Dict[str, Any]] = {}
    for row in persisted.get("results") or []:
        if not isinstance(row, dict):
            continue
        name = str(row.get("model") or "").strip()
        if name and is_skippable_done_row(row):
            by_name[name] = dict(row)

    merged: List[Dict[str, Any]] = []
    models_to_run: List[str] = []
    for name in installed_chat_models:
        if name in by_name:
            merged.append(dict(by_name[name]))
            continue
        merged.append(_pending_row(name))
        models_to_run.append(name)

    return merged, models_to_run


def _attach_resume_meta(snap: Dict[str, Any], url: Optional[str] = None) -> Dict[str, Any]:
    meta = can_resume_benchmark(url)
    return {**snap, **meta}


def _parse_persisted_raw(raw: Optional[str]) -> Optional[Dict[str, Any]]:
    if not raw:
        return None
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        return None


def _load_persisted_at_key(storage_key: str) -> Optional[Dict[str, Any]]:
    return _parse_persisted_raw(state.storage.get_setting(storage_key))


def _migrate_persisted_to_canonical(data: Dict[str, Any], canon_url: str, found_key: str) -> Dict[str, Any]:
    canon_key = _storage_key(canon_url)
    if found_key != canon_key:
        try:
            state.storage.set_setting(canon_key, json.dumps(data, default=str))
        except Exception as exc:
            logger.warning("benchmark migrate to canonical key failed: %s", exc)
    return data


def _load_persisted_with_fallback(url: str) -> Optional[Dict[str, Any]]:
    canon = benchmark_storage_url(url)
    keys_to_try: List[str] = [_storage_key(canon)]
    for alt in _ollama_legacy_url_alternates(canon):
        keys_to_try.append(f"{BENCHMARK_KEY_PREFIX}{alt}")
    # Legacy keys before canonical normalization (mixed case / trailing slash)
    raw_url = (url or "").strip().rstrip("/")
    if raw_url:
        keys_to_try.append(f"{BENCHMARK_KEY_PREFIX}{raw_url}"[:200])

    seen: set[str] = set()
    for key in keys_to_try:
        if key in seen:
            continue
        seen.add(key)
        data = _load_persisted_at_key(key)
        if data:
            return _migrate_persisted_to_canonical(data, canon, key)

    try:
        for key in state.storage.list_settings_keys_prefix(BENCHMARK_KEY_PREFIX):
            if key in seen:
                continue
            data = _load_persisted_at_key(key)
            if data and isinstance(data.get("results"), list) and data["results"]:
                return _migrate_persisted_to_canonical(data, canon, key)
    except Exception as exc:
        logger.warning("benchmark settings scan failed: %s", exc)
    return None


def _load_persisted(url: str) -> Optional[Dict[str, Any]]:
    return _load_persisted_with_fallback(url)


def is_benchmark_running() -> bool:
    with _JOB_LOCK:
        return _JOB is not None and _JOB.get("status") == "running"


def _persist_snapshot(
    snapshot: Dict[str, Any],
    url: str,
    backup_reason: str = "persist",
) -> None:
    canon_url = benchmark_storage_url(url)
    try:
        state.storage.set_setting(
            _storage_key(canon_url),
            json.dumps(snapshot, default=str),
        )
        from backend.services.model_benchmark_backup import write_benchmark_backup

        write_benchmark_backup(snapshot, canon_url, backup_reason)
    except Exception as exc:
        logger.warning("benchmark persist failed for %s: %s", canon_url, exc)


def _persist_job_snapshot(url: str, backup_reason: str) -> None:
    with _JOB_LOCK:
        if _JOB is None:
            return
        snap = _snapshot_locked()
        snap.pop("_startedMono", None)
    _persist_snapshot(snap, url, backup_reason)


def _normalize_import_row(row: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(row)
    model = str(out.get("model") or "").strip()
    if not model:
        return out
    out["model"] = model
    for score_key in ("toolScore", "codeScore", "overallScore"):
        val = out.get(score_key)
        if val is not None and not isinstance(val, int):
            try:
                out[score_key] = int(round(float(val)))
            except (TypeError, ValueError):
                pass
    if out.get("toolScore") is not None and not out.get("toolTier"):
        out["toolTier"] = score_tier(out.get("toolScore"))
    if out.get("codeScore") is not None and not out.get("codeTier"):
        out["codeTier"] = score_tier(out.get("codeScore"))
    return out


def import_benchmark_snapshot(
    data: Dict[str, Any],
    url: Optional[str] = None,
) -> Dict[str, Any]:
    if is_benchmark_running():
        return {"ok": False, "status": "blocked", "error": "Benchmark is running"}
    if not isinstance(data, dict):
        return {"ok": False, "status": "blocked", "error": "Invalid snapshot"}
    rows_in = data.get("results")
    if not isinstance(rows_in, list):
        return {"ok": False, "status": "blocked", "error": "Snapshot must include results array"}

    storage_url = benchmark_storage_url(
        str(url or data.get("storageUrl") or data.get("url") or resolve_benchmark_url())
    )
    results = [_normalize_import_row(r) for r in rows_in if isinstance(r, dict)]
    done_count, remaining_count = _resume_counts(results)
    if remaining_count > 0 and done_count > 0:
        status = "cancelled"
    elif remaining_count == 0 and results:
        status = "done"
    else:
        status = str(data.get("status") or "done")

    snapshot: Dict[str, Any] = {
        "runId": str(data.get("runId") or uuid.uuid4().hex[:12]),
        "status": status,
        "startedAt": str(data.get("startedAt") or datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
        "total": len(results),
        "completed": sum(
            1 for r in results if r.get("status") in ("done", "failed")
        ),
        "currentModel": None,
        "phase": "idle",
        "codingTaskIndex": 0,
        "codingTaskTotal": int(data.get("codingTaskTotal") or 8),
        "results": results,
        "bestModel": pick_best_model(results),
        "cancelRequested": False,
        "url": storage_url,
        "storageUrl": storage_url,
    }
    _persist_snapshot(snapshot, storage_url, "import")
    return {**snapshot, "fromPersisted": True, **can_resume_benchmark(storage_url)}


def export_benchmark_snapshot(url: Optional[str] = None) -> Dict[str, Any]:
    storage_url = resolve_benchmark_url(url)
    with _JOB_LOCK:
        snap = _snapshot_locked()
    if snap.get("status") == "running":
        out = dict(snap)
        out["storageUrl"] = benchmark_storage_url(str(snap.get("url") or storage_url))
        return out
    persisted = _load_persisted_with_fallback(storage_url)
    if persisted:
        out = {**persisted, "storageUrl": storage_url}
        return out
    return {"storageUrl": storage_url, "results": [], "status": "idle"}


def benchmark_blocked_reason() -> Optional[str]:
    from backend.agents.agent_run import get_active_run

    if get_active_run() is not None:
        return "An agent sprint step is running. Stop the sprint before benchmarking."
    if str(getattr(state, "ACTIVE_SPRINT_AGENT", "") or "").strip():
        return "A sprint is active. Stop auto-sprint before benchmarking."
    return None


def _pending_row(model: str) -> Dict[str, Any]:
    return {
        "model": model,
        "status": "pending",
        "loadOk": None,
        "toolScore": None,
        "toolTier": "unknown",
        "toolPassRate": None,
        "toolSummary": None,
        "codeScore": None,
        "codeTier": "unknown",
        "codePassRate": None,
        "nativeToolCallRate": None,
        "evalSummary": None,
        "overallScore": None,
        "wallClockSec": None,
        "error": None,
    }


def _snapshot_locked() -> Dict[str, Any]:
    if _JOB is None:
        return {
            "runId": None,
            "status": "idle",
            "total": 0,
            "completed": 0,
            "currentModel": None,
            "phase": "idle",
            "codingTaskIndex": 0,
            "codingTaskTotal": 0,
            "results": [],
            "bestModel": None,
            "cancelRequested": False,
        }
    job = dict(_JOB)
    job["results"] = [dict(row) for row in _JOB.get("results") or []]
    if "_startedMono" in _JOB:
        job["elapsedMs"] = int((time.perf_counter() - _JOB["_startedMono"]) * 1000)
    job.pop("_startedMono", None)
    return job


def get_benchmark_snapshot() -> Dict[str, Any]:
    with _JOB_LOCK:
        snap = _snapshot_locked()
    url = resolve_benchmark_url(
        str(snap.get("url") or "") if snap.get("url") else None
    )
    if snap.get("status") == "idle":
        persisted = _load_persisted_with_fallback(url)
        if persisted:
            out = {
                **persisted,
                "status": persisted.get("status") or "done",
                "fromPersisted": True,
                "storageUrl": url,
            }
            return _attach_resume_meta(out, url)
    if snap.get("status") == "running":
        snap = {**snap, "storageUrl": benchmark_storage_url(str(snap.get("url") or url))}
    return _attach_resume_meta(snap, url)


def _set_phase(phase: str, **extra: Any) -> None:
    with _JOB_LOCK:
        if _JOB is None:
            return
        _JOB["phase"] = phase
        for k, v in extra.items():
            _JOB[k] = v


def _update_row(model: str, **fields: Any) -> None:
    with _JOB_LOCK:
        if _JOB is None:
            return
        for row in _JOB.get("results") or []:
            if row.get("model") == model:
                row.update(fields)
                break
        done = sum(1 for r in _JOB["results"] if r.get("status") == "done")
        failed = sum(1 for r in _JOB["results"] if r.get("status") == "failed")
        _JOB["completed"] = done + failed
        _JOB["bestModel"] = pick_best_model(_JOB["results"])


def _should_cancel() -> bool:
    global _CANCEL_REQUESTED
    return _CANCEL_REQUESTED


def cancel_benchmark() -> Dict[str, Any]:
    global _CANCEL_REQUESTED
    with _JOB_LOCK:
        _CANCEL_REQUESTED = True
        if _JOB is not None:
            _JOB["cancelRequested"] = True
    return get_benchmark_snapshot()


def _set_dev_model(model: str) -> Dict[str, str]:
    from backend.agents.registry import agent_dev

    prior = {
        "dev": str(getattr(agent_dev, "model", "") or state.PRIMARY_MODELS.get("dev") or ""),
    }
    agent_dev.set_primary_model(model)
    state.PRIMARY_MODELS["dev"] = model
    return prior


def _restore_dev_model(prior: Dict[str, str]) -> None:
    from backend.agents.registry import agent_dev

    model = str(prior.get("dev") or "")
    if model:
        agent_dev.set_primary_model(model)
        state.PRIMARY_MODELS["dev"] = model


def _run_benchmark(models: List[str], url: Optional[str], agent_id: str) -> None:
    global _CANCEL_REQUESTED  # noqa: PLW0603
    from backend.services.eval_harness import DEFAULT_TASKS_DIR, load_tasks, run_suite
    from backend.services.model_test_runner import build_test_provider, probe_model
    from backend.services.tool_llm_probe import run_llm_probe_all

    provider = build_test_provider(url)
    health = provider.health()
    tasks = load_tasks(DEFAULT_TASKS_DIR)
    task_total = len(tasks)
    ollama_url = benchmark_storage_url(health.url if health.ok else (url or ""))

    def _persist_progress() -> None:
        with _JOB_LOCK:
            if _JOB is None:
                return
            progress = _snapshot_locked()
            progress.pop("_startedMono", None)
            _persist_snapshot(progress, ollama_url, "progress")

    try:
        for model in models:
            if _should_cancel():
                break
            started = time.perf_counter()
            _set_phase("loading", currentModel=model)
            _update_row(model, status="running")

            load_result = probe_model(provider, health, model)
            load_ok = bool(load_result.get("ok"))
            if not load_ok:
                _update_row(
                    model,
                    status="failed",
                    loadOk=False,
                    error=str(load_result.get("error") or "load failed")[:400],
                    wallClockSec=round(time.perf_counter() - started, 1),
                )
                _persist_progress()
                continue

            if _should_cancel():
                break

            _set_phase("tools", currentModel=model)
            probe_results = run_llm_probe_all(agent_id, model=model, include_destructive=False)
            probe_pass = sum(1 for r in probe_results if r.get("success"))
            probe_fail = sum(1 for r in probe_results if not r.get("success") and not r.get("skipReason"))
            probe_skip = sum(1 for r in probe_results if r.get("skipReason"))
            tool_summary = {
                "pass": probe_pass,
                "fail": probe_fail,
                "skip": probe_skip,
                "total": len(probe_results),
            }
            tool_score, tool_rate = compute_tool_scores(tool_summary)

            if _should_cancel():
                break

            _set_phase("coding", currentModel=model, codingTaskIndex=0, codingTaskTotal=task_total)
            prior_dev = _set_dev_model(model)

            def on_eval(_result: Any) -> None:
                with _JOB_LOCK:
                    if _JOB is None:
                        return
                    idx = int(_JOB.get("codingTaskIndex") or 0) + 1
                    _JOB["codingTaskIndex"] = min(idx, task_total)

            try:
                _results, eval_summary = run_suite(tasks, on_result=on_eval)
            finally:
                _restore_dev_model(prior_dev)

            code_score, pass_rate, native = compute_code_scores(eval_summary)
            overall = compute_overall_score(tool_score, code_score)
            wall = round(time.perf_counter() - started, 1)

            _update_row(
                model,
                status="done",
                loadOk=True,
                toolScore=tool_score,
                toolTier=score_tier(tool_score),
                toolPassRate=round(tool_rate, 3),
                toolSummary=tool_summary,
                codeScore=code_score,
                codeTier=score_tier(code_score),
                codePassRate=round(pass_rate, 3),
                nativeToolCallRate=round(native, 3),
                evalSummary=eval_summary,
                overallScore=overall,
                wallClockSec=wall,
                error=None,
            )
            _persist_progress()

        with _JOB_LOCK:
            if _JOB is None:
                return
            _JOB["status"] = "cancelled" if _CANCEL_REQUESTED else "done"
            _JOB["currentModel"] = None
            _JOB["phase"] = "idle"
            _JOB["bestModel"] = pick_best_model(_JOB.get("results") or [])
            finished = _snapshot_locked()
            finished.pop("_startedMono", None)
            _persist_snapshot(finished, ollama_url, "finished")
    except Exception as exc:
        with _JOB_LOCK:
            if _JOB is not None:
                _JOB["status"] = "done"
                _JOB["error"] = f"{type(exc).__name__}: {exc}"[:500]
                _JOB["currentModel"] = None
                _JOB["phase"] = "idle"
        _persist_job_snapshot(ollama_url, "exception")
    finally:
        _CANCEL_REQUESTED = False


def start_model_benchmark(
    models: Optional[List[str]] = None,
    *,
    url: Optional[str] = None,
    agent_id: str = "dev",
    resume: bool = False,
) -> Dict[str, Any]:
    """Start background benchmark; returns snapshot (409 via API if blocked)."""
    global _JOB, _CANCEL_REQUESTED

    block = benchmark_blocked_reason()
    if block:
        return {"ok": False, "status": "blocked", "error": block}

    from backend.services.model_test_runner import build_test_provider

    provider = build_test_provider(url)
    health = provider.health()
    if not health.ok:
        return {
            "ok": False,
            "status": "blocked",
            "error": health.error or "LLM server unreachable",
        }

    model_list = filter_chat_models(list(models or health.models or []))
    if not model_list:
        return {"ok": False, "status": "blocked", "error": "No chat models to benchmark"}

    merged_results = [_pending_row(m) for m in model_list]
    models_to_run = list(model_list)
    resumed_from: Optional[str] = None
    started_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    if resume:
        persisted = _load_persisted_with_fallback(health.url)
        if not persisted:
            return {"ok": False, "status": "blocked", "error": "Nothing to resume — no saved benchmark"}
        merged_results, models_to_run = merge_resume_benchmark(persisted, model_list)
        if not models_to_run:
            return {"ok": False, "status": "blocked", "error": "Nothing to resume — all models already done"}
        resumed_from = str(persisted.get("runId") or "")
        started_at = str(persisted.get("startedAt") or started_at)
    done_prefill, _ = _resume_counts(merged_results)

    with _JOB_LOCK:
        if _JOB is not None and _JOB.get("status") == "running":
            return _snapshot_locked()
        _CANCEL_REQUESTED = False
        _JOB = {
            "runId": uuid.uuid4().hex[:12],
            "status": "running",
            "startedAt": started_at,
            "_startedMono": time.perf_counter(),
            "total": len(merged_results),
            "completed": done_prefill,
            "currentModel": None,
            "phase": "idle",
            "codingTaskIndex": 0,
            "codingTaskTotal": 0,
            "results": merged_results,
            "bestModel": pick_best_model(merged_results),
            "cancelRequested": False,
            "agentId": agent_id,
            "url": health.url,
            "resume": bool(resume),
            "resumedFromRunId": resumed_from,
        }
        snapshot = _snapshot_locked()

    threading.Thread(
        target=_run_benchmark,
        args=(models_to_run, url, agent_id),
        name="model-benchmark",
        daemon=True,
    ).start()
    return _attach_resume_meta(snapshot, health.url)


def reset_benchmark_job_state() -> None:
    """Test hook."""
    global _JOB, _CANCEL_REQUESTED
    with _JOB_LOCK:
        _JOB = None
        _CANCEL_REQUESTED = False
