"""Compare configured Developer model against persisted benchmark scores."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from backend import state
from backend.services.logs import add_system_log
from backend.services.workflow_settings import get_workflow_settings


@dataclass
class DevModelAssessment:
    level: str  # ok | warn | block
    reasons: List[str] = field(default_factory=list)
    best_model: str = ""
    row: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "level": self.level,
            "reasons": list(self.reasons),
            "bestModel": self.best_model,
            "row": self.row,
        }


def _benchmark_rows(url: Optional[str] = None) -> List[Dict[str, Any]]:
    from backend.services.model_benchmark_runner import get_benchmark_snapshot, resolve_benchmark_url

    snap = get_benchmark_snapshot()
    if url:
        _ = resolve_benchmark_url(url)
    results = snap.get("results") if isinstance(snap.get("results"), list) else []
    return [r for r in results if isinstance(r, dict)]


def lookup_benchmark_row(model: str, url: Optional[str] = None) -> Optional[Dict[str, Any]]:
    name = str(model or "").strip()
    if not name:
        return None
    for row in _benchmark_rows(url):
        if str(row.get("model") or "").strip() == name:
            if str(row.get("status") or "") == "done":
                return row
    return None


def _thresholds() -> Dict[str, Any]:
    ws = get_workflow_settings()
    return {
        "min_overall_warn": int(ws.get("devBenchmarkMinOverallWarn") or 50),
        "min_code_warn": int(ws.get("devBenchmarkMinCodeWarn") or 45),
        "min_native_warn": float(ws.get("devBenchmarkMinNativeWarn") or 0.35),
        "best_gap_warn": int(ws.get("devBenchmarkBestGapWarn") or 12),
        "block_below_overall": int(ws.get("devBenchmarkBlockBelowOverall") or 0),
    }


def assess_dev_model(model: str, url: Optional[str] = None) -> DevModelAssessment:
    """Warn/block when benchmark data suggests a poor Developer primary model."""
    from backend.services.model_benchmark_runner import get_benchmark_snapshot, pick_best_model

    name = str(model or "").strip()
    if not name:
        return DevModelAssessment(level="ok")

    snap = get_benchmark_snapshot()
    rows = _benchmark_rows(url)
    if not rows:
        return DevModelAssessment(level="ok")

    row = lookup_benchmark_row(name, url)
    best = str(snap.get("bestModel") or pick_best_model(rows) or "").strip()
    t = _thresholds()
    reasons: List[str] = []
    level = "ok"

    if row is None:
        return DevModelAssessment(level="ok", best_model=best)

    tool_score = int(row.get("toolScore") or 0)
    overall = row.get("overallScore")
    code = row.get("codeScore")
    native = row.get("nativeToolCallRate")
    load_ok = row.get("loadOk")

    if load_ok is False:
        reasons.append("benchmark load failed for this model")
        level = "warn"
    if tool_score == 0:
        reasons.append("benchmark Tools score is 0 (no native tool probes passed)")
        level = "warn"

    if isinstance(overall, int) and overall < t["min_overall_warn"]:
        reasons.append(f"benchmark overall {overall} < {t['min_overall_warn']}")
        level = "warn"
    if isinstance(code, int) and code < t["min_code_warn"]:
        reasons.append(f"benchmark Code {code} < {t['min_code_warn']}")
        level = "warn"
    if native is not None:
        try:
            native_f = float(native)
            if native_f < t["min_native_warn"]:
                reasons.append(
                    f"benchmark native tool-call rate {native_f:.2f} < {t['min_native_warn']}"
                )
                level = "warn"
        except (TypeError, ValueError):
            pass

    if best and best != name:
        best_row = lookup_benchmark_row(best, url)
        if best_row and isinstance(row.get("overallScore"), int) and isinstance(
            best_row.get("overallScore"), int
        ):
            gap = int(best_row["overallScore"]) - int(row["overallScore"])
            if gap >= t["best_gap_warn"]:
                reasons.append(
                    f"benchmark best is {best} (overall {best_row['overallScore']}) "
                    f"— gap {gap} vs your Dev model"
                )
                level = "warn"

    block_floor = int(t["block_below_overall"] or 0)
    if block_floor > 0 and isinstance(overall, int) and overall < block_floor:
        reasons.append(f"overall {overall} below devBenchmarkBlockBelowOverall ({block_floor})")
        level = "block"

    return DevModelAssessment(level=level, reasons=reasons, best_model=best, row=row)


def dev_benchmark_blocks_sprint() -> Optional[str]:
    """When devBenchmarkBlockBelowOverall is set, return a message that blocks sprint start."""
    dev = str((getattr(state, "PRIMARY_MODELS", {}) or {}).get("dev") or "").strip()
    if not dev:
        return None
    assessment = assess_dev_model(dev)
    if assessment.level != "block":
        return None
    detail = "; ".join(assessment.reasons[:4]) or "benchmark block threshold"
    return f"Dev model {dev}: {detail}"


def warn_dev_model_benchmark_mismatch() -> None:
    """Log once per sprint start when Dev primary looks weak vs benchmark."""
    dev = str((getattr(state, "PRIMARY_MODELS", {}) or {}).get("dev") or "").strip()
    if not dev:
        return
    assessment = assess_dev_model(dev)
    if assessment.level == "ok" or not assessment.reasons:
        return
    prefix = "Dev model benchmark" if assessment.level == "warn" else "Dev model blocked by benchmark"
    detail = "; ".join(assessment.reasons[:4])
    best = assessment.best_model
    hint = f" Consider {best}." if best and best != dev else ""
    add_system_log(
        "System",
        "warning" if assessment.level == "warn" else "error",
        f"{prefix}: {dev} — {detail}.{hint}",
    )


def dev_model_assessment_payload(model: Optional[str] = None) -> Dict[str, Any]:
    dev = str(model or (getattr(state, "PRIMARY_MODELS", {}) or {}).get("dev") or "").strip()
    assessment = assess_dev_model(dev)
    return {
        "devModel": dev,
        "assessment": assessment.to_dict(),
        "bestModel": assessment.best_model,
        "row": assessment.row,
    }
