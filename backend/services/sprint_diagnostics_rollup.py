"""Aggregate step diagnostics JSON into sprint health rollups."""

from __future__ import annotations

import json
import time
from collections import Counter, defaultdict
from pathlib import Path
from statistics import median
from typing import Any, Dict, List, Optional

from backend import state
from backend.config import diagnostics_dir
from backend.services.build_info import get_app_build_info
from backend.services.sprint_speed_gates import UNHEALTHY_LANE_ADVANCE_EXITS

DEFAULT_LIMIT = 50
DEFAULT_SINCE_HOURS = 72
RECENT_STEPS_LIST = 10


def _load_completed_traces(
    project_id: Optional[str],
    *,
    limit: int,
    since_hours: float,
) -> List[Dict[str, Any]]:
    pid = project_id or state.CURRENT_PROJECT_ID
    if not pid:
        return []
    try:
        folder = diagnostics_dir(pid)
    except Exception:
        return []
    if not folder.is_dir():
        return []

    cutoff = time.time() - max(0.0, since_hours) * 3600.0
    candidates: List[tuple[float, Path]] = []
    for path in folder.glob("step-*.json"):
        try:
            mtime = path.stat().st_mtime
            if mtime < cutoff:
                continue
            candidates.append((mtime, path))
        except OSError:
            continue

    candidates.sort(key=lambda x: x[0], reverse=True)
    out: List[Dict[str, Any]] = []
    for _mtime, path in candidates[: max(1, limit)]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if str(payload.get("status") or "") != "complete":
            continue
        out.append(payload)
    return out


def build_sprint_diagnostics_rollup(
    *,
    project_id: Optional[str] = None,
    limit: int = DEFAULT_LIMIT,
    since_hours: float = DEFAULT_SINCE_HOURS,
    duplicate_summary: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Roll up exit reasons and refusal/deadlock signals from recent step traces."""
    traces = _load_completed_traces(
        project_id,
        limit=max(1, int(limit or DEFAULT_LIMIT)),
        since_hours=float(since_hours or DEFAULT_SINCE_HOURS),
    )

    exit_counts: Counter[str] = Counter()
    refusal_counts: Counter[str] = Counter()
    deadlock_true = 0
    deadlock_false = 0
    deadlock_reasons: Counter[str] = Counter()
    ok_count = 0
    durations: List[int] = []
    cursor_v2 = 0
    bad_by_task: Dict[str, int] = defaultdict(int)
    dev_precheck_skip_steps = 0
    zero_token_dev_steps = 0
    writes_attempted = 0
    writes_succeeded = 0
    visit_cap_latch_steps = 0
    dev_model_refusal_steps: Counter[str] = Counter()

    for payload in traces:
        reason = str(payload.get("exitReason") or "unknown").strip() or "unknown"
        exit_counts[reason] += 1
        events = payload.get("events") or []
        if reason == "dev_precheck_skip" or any(
            isinstance(e, dict) and str(e.get("kind") or "") == "dev_precheck_skip"
            for e in events
        ):
            dev_precheck_skip_steps += 1
        if str(payload.get("agent") or "") == "Developer":
            if int(payload.get("ollamaCallCount") or 0) == 0 and int(
                payload.get("promptTokensTotal") or 0
            ) == 0:
                zero_token_dev_steps += 1
        if payload.get("ok"):
            ok_count += 1
        dur = int(payload.get("durationMs") or 0)
        if dur > 0:
            durations.append(dur)
        if payload.get("cursorLikenessScoreV2"):
            cursor_v2 += 1

        rc = str(payload.get("textRefusalClass") or "").strip()
        if rc and rc != "none":
            refusal_counts[rc] += 1
        if (
            str(payload.get("agent") or "") == "Developer"
            and rc in ("safety_refusal", "meta_refusal")
        ):
            for call in payload.get("ollamaCalls") or []:
                if isinstance(call, dict):
                    model_name = str(call.get("modelUsed") or "").strip()
                    if model_name:
                        dev_model_refusal_steps[model_name] += 1

        if payload.get("toolPolicyDeadlock"):
            deadlock_true += 1
            r = str(payload.get("toolPolicyDeadlockReason") or "").strip()
            if r:
                deadlock_reasons[r[:120]] += 1
        else:
            deadlock_false += 1

        tid = str(payload.get("taskId") or "").strip()
        if tid and reason in UNHEALTHY_LANE_ADVANCE_EXITS:
            bad_by_task[tid] += 1

        writes_attempted += int(payload.get("writesAttempted") or 0)
        writes_succeeded += int(payload.get("writesSucceeded") or 0)
        ccs = payload.get("cardCumulativeState") or {}
        if isinstance(ccs, dict) and ccs.get("phaseCycleCapReached"):
            visit_cap_latch_steps += 1
        if reason == "dev_precheck_skip":
            visit_cap_latch_steps += 1

    steps_total = len(traces)
    top_tasks = sorted(
        (
            {
                "taskId": tid,
                "taskTitle": _title_for_task(traces, tid),
                "unhealthySteps": n,
            }
            for tid, n in bad_by_task.items()
        ),
        key=lambda x: (-int(x["unhealthySteps"]), x["taskId"]),
    )[:12]

    recent_steps: List[Dict[str, Any]] = []
    for payload in traces[:RECENT_STEPS_LIST]:
        from backend.services.step_diagnostics import infer_primary_bottleneck_from_payload

        bottleneck = infer_primary_bottleneck_from_payload(payload)
        native_rate = payload.get("nativeToolCallRate")
        if native_rate is None and str(payload.get("agent") or "") == "Developer":
            calls = payload.get("ollamaCalls") or []
            if calls:
                native_n = sum(
                    1
                    for c in calls
                    if isinstance(c, dict) and (c.get("toolCalls") or [])
                )
                native_rate = native_n / max(1, len(calls))
        recent_steps.append(
            {
                "taskId": payload.get("taskId"),
                "taskTitle": payload.get("taskTitle"),
                "exitReason": payload.get("exitReason"),
                "textRefusalClass": payload.get("textRefusalClass"),
                "toolPolicyDeadlock": bool(payload.get("toolPolicyDeadlock")),
                "durationMs": int(payload.get("durationMs") or 0),
                "endedAt": payload.get("endedAt"),
                "resolvedEditTarget": payload.get("resolvedEditTarget"),
                "backupSwitchOutcome": payload.get("backupSwitchOutcome"),
                "primaryBottleneck": bottleneck,
                "nativeToolCallRate": native_rate,
            }
        )

    dup = duplicate_summary or {}
    build = get_app_build_info()
    if traces:
        app_from_trace = (traces[0].get("appBuild") or {}) if isinstance(traces[0], dict) else {}
        if isinstance(app_from_trace, dict) and app_from_trace.get("gitSha"):
            build = {**build, **app_from_trace}

    from backend.services.sprint_throughput import enrich_rollup_throughput

    base = {
        "projectId": project_id or state.CURRENT_PROJECT_ID,
        "window": {
            "limit": int(limit),
            "sinceHours": float(since_hours),
            "stepsIncluded": steps_total,
        },
        "appBuild": build,
        "stepsTotal": steps_total,
        "okRate": round(ok_count / steps_total, 3) if steps_total else 0.0,
        "medianDurationMs": int(median(durations)) if durations else None,
        "cursorLikenessV2Rate": round(cursor_v2 / steps_total, 3) if steps_total else 0.0,
        "exitReason": dict(exit_counts.most_common()),
        "textRefusalClass": dict(refusal_counts.most_common()),
        "devModelRefusalSteps": dict(dev_model_refusal_steps.most_common(8)),
        "toolPolicyDeadlock": {
            "true": deadlock_true,
            "false": deadlock_false,
            "topReasons": [
                {"reason": r, "count": c} for r, c in deadlock_reasons.most_common(8)
            ],
        },
        "topTasksByBadSteps": top_tasks,
        "recentSteps": recent_steps,
        "duplicateClusterCount": int(dup.get("duplicateClusterCount") or 0),
        "duplicateExtraCount": int(dup.get("duplicateExtraCount") or 0),
        "devPrecheckSkipSteps": dev_precheck_skip_steps,
        "zeroTokenDevSteps": zero_token_dev_steps,
        "writesAttempted": writes_attempted,
        "writesSucceeded": writes_succeeded,
        "writesPerStep": round(writes_succeeded / steps_total, 3) if steps_total else 0.0,
        "visitCapLatchStepRate": (
            round(visit_cap_latch_steps / steps_total, 3) if steps_total else 0.0
        ),
    }
    return enrich_rollup_throughput(base)


def _title_for_task(traces: List[Dict[str, Any]], task_id: str) -> str:
    for payload in traces:
        if str(payload.get("taskId") or "") == task_id:
            return str(payload.get("taskTitle") or task_id)
    return task_id
