"""Soft spec-readiness checks for spec-driven development (non-blocking)."""

from __future__ import annotations

from typing import Any, Dict, List

from backend.agents.task_context import coerce_task_text, normalize_acceptance_criteria


def _scope_lines(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [coerce_task_text(v).strip() for v in value if coerce_task_text(v).strip()]
    text = coerce_task_text(value).strip()
    if not text:
        return []
    return [ln.strip() for ln in text.splitlines() if ln.strip()]


def spec_readiness(task: Dict[str, Any]) -> Dict[str, Any]:
    """Return { ok, missing, warnings } for Dev-ready SDD shape."""
    missing: List[str] = []
    warnings: List[str] = []

    title = coerce_task_text(task.get("title")).strip()
    desc = coerce_task_text(task.get("description")).strip()
    work_type = str(task.get("workType") or "implementation").strip().lower()
    ac = normalize_acceptance_criteria(task.get("acceptanceCriteria"))

    if not title:
        missing.append("title")
    if not desc:
        missing.append("description")
    if not work_type:
        missing.append("workType")

    min_ac = 1
    if work_type == "implementation":
        min_ac = 2
    if work_type in ("planning", "spike"):
        min_ac = 0

    if len(ac) < min_ac and work_type not in ("planning", "spike"):
        missing.append(
            f"acceptanceCriteria (need ≥{min_ac}, have {len(ac)})"
        )

    if work_type == "implementation" and not coerce_task_text(task.get("userStory")).strip():
        warnings.append("userStory empty (recommended: As a … I want … so that …)")

    if work_type == "implementation" and not _scope_lines(task.get("scope")):
        warnings.append("scope empty (recommended: in-scope bullets)")

    if work_type == "implementation" and not coerce_task_text(task.get("testPlan")).strip():
        warnings.append("testPlan empty (recommended: verify commands or steps)")

    if work_type == "implementation" and len(ac) > 5:
        warnings.append(
            f"acceptanceCriteria count {len(ac)} > 5 — split card or add_subtasks before Dev"
        )
    if work_type == "implementation" and len(ac) > 3:
        warnings.append(
            f"acceptanceCriteria count {len(ac)} > 3 — prefer ≤3 AC per implementation card"
        )

    ok = len(missing) == 0
    return {"ok": ok, "missing": missing, "warnings": warnings}
