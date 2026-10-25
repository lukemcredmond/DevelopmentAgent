"""Sprint throughput metrics: LOC, Done/hour, writes/step."""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from backend import state


def workspace_loc_snapshot(workspace_dir: Optional[str] = None) -> Dict[str, int]:
    """Count lines in workspace source files (best-effort, no git required)."""
    root = Path(str(workspace_dir or state.WORKSPACE_DIR or "")).expanduser()
    if not root.is_dir():
        return {"dartLines": 0, "sourceLines": 0, "dartFiles": 0}

    dart_lines = 0
    dart_files = 0
    source_lines = 0
    source_suffixes = {".dart", ".yaml", ".yml", ".json"}

    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for name in filenames:
            if name.startswith("."):
                continue
            path = Path(dirpath) / name
            if path.suffix == ".dart":
                try:
                    dart_lines += sum(1 for _ in path.open(encoding="utf-8", errors="replace"))
                    dart_files += 1
                except OSError:
                    pass
            if path.suffix.lower() in source_suffixes:
                try:
                    source_lines += sum(1 for _ in path.open(encoding="utf-8", errors="replace"))
                except OSError:
                    pass
    return {
        "dartLines": dart_lines,
        "sourceLines": source_lines,
        "dartFiles": dart_files,
    }


def _parse_report_ts(raw: Optional[str]) -> Optional[datetime]:
    if not raw:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(str(raw)[:19], fmt)
        except ValueError:
            continue
    return None


def _done_delta(report: Dict[str, Any]) -> int:
    start = (report.get("laneCountsStart") or {}).get("Done") or 0
    end = (report.get("laneCountsEnd") or {}).get("Done") or 0
    return int(end) - int(start)


def _needs_po_delta(report: Dict[str, Any]) -> int:
    start = (report.get("laneCountsStart") or {}).get("Needs PO") or 0
    end = (report.get("laneCountsEnd") or {}).get("Needs PO") or 0
    return int(end) - int(start)


def sprint_report_throughput(report: Dict[str, Any]) -> Dict[str, Any]:
    """Per-sprint lane deltas and Done/hour from a finished or running report."""
    started = _parse_report_ts(report.get("startedAt"))
    ended = _parse_report_ts(report.get("endedAt")) or datetime.now()
    hours = 0.0
    if started:
        hours = max((ended - started).total_seconds() / 3600.0, 1.0 / 60.0)
    done_delta = _done_delta(report)
    steps = int(report.get("stepsRun") or 0)
    loc_start = report.get("workspaceLocStart") or {}
    loc_end = report.get("workspaceLocEnd") or loc_start
    dart_start = int(loc_start.get("dartLines") or 0) if isinstance(loc_start, dict) else 0
    dart_end = int(loc_end.get("dartLines") or 0) if isinstance(loc_end, dict) else dart_start
    return {
        "doneDelta": done_delta,
        "needsPoDelta": _needs_po_delta(report),
        "donePerHour": round(done_delta / hours, 2) if hours else 0.0,
        "stepsRun": steps,
        "dartLocDelta": dart_end - dart_start,
        "workspaceLocEnd": loc_end if isinstance(loc_end, dict) else {},
    }


def enrich_rollup_throughput(rollup: Dict[str, Any]) -> Dict[str, Any]:
    """Attach workspace LOC snapshot to rollup (writes/visit-cap computed in rollup builder)."""
    out = dict(rollup)
    out["workspaceLoc"] = workspace_loc_snapshot()
    return out


def build_support_throughput_summary(
    *,
    project_id: Optional[str] = None,
    rollup: Optional[Dict[str, Any]] = None,
    reports: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Compact throughput block for support bundle and API."""
    from backend.services.sprint_report import current_sprint_report, list_sprint_reports

    pid = project_id or state.CURRENT_PROJECT_ID
    hist = reports if reports is not None else list_sprint_reports(pid)
    current = current_sprint_report()
    sprint_rows = []
    for rep in (hist or [])[:10]:
        if isinstance(rep, dict):
            sprint_rows.append({**sprint_report_throughput(rep), "status": rep.get("status")})
    if isinstance(current, dict):
        sprint_rows.insert(0, {**sprint_report_throughput(current), "status": current.get("status")})
    out: Dict[str, Any] = {
        "projectId": pid,
        "workspaceLoc": workspace_loc_snapshot(),
        "recentSprints": sprint_rows,
    }
    if rollup:
        out["diagnosticsWindow"] = rollup.get("window")
        out["okRate"] = rollup.get("okRate")
        out["writesPerStep"] = rollup.get("writesPerStep")
        out["visitCapLatchStepRate"] = rollup.get("visitCapLatchStepRate")
        out["writesSucceeded"] = rollup.get("writesSucceeded")
    return out
