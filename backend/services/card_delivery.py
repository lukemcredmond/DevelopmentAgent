"""Expected vs actual delivery tracking on Kanban cards."""

from __future__ import annotations

import datetime
from typing import Any, Dict, List, Optional

from backend.agents.task_context import coerce_task_text, normalize_acceptance_criteria


def _now() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _truncate(text: str, limit: int = 400) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def build_expected_summary(task: Dict[str, Any]) -> str:
    title = coerce_task_text(task.get("title")).strip()
    desc = coerce_task_text(task.get("description")).strip()
    test_plan = coerce_task_text(task.get("testPlan")).strip()
    ac = normalize_acceptance_criteria(task.get("acceptanceCriteria"))
    parts: List[str] = []
    if title:
        parts.append(f"Deliver: {title}")
    if desc:
        parts.append(desc[:500])
    if ac:
        parts.append("Acceptance: " + "; ".join(ac[:8]))
    if test_plan:
        parts.append(f"Verify: {test_plan[:300]}")
    return _truncate("\n".join(parts), 1200) or "(No expected summary — add description and AC.)"


def sync_ac_verification(task: Dict[str, Any]) -> None:
    acs = normalize_acceptance_criteria(task.get("acceptanceCriteria"))
    checks = list(task.get("acChecklist") or [])
    while len(checks) < len(acs):
        checks.append(False)
    checks = [bool(x) for x in checks[: len(acs)]]
    task["acChecklist"] = checks

    prior: Dict[str, Dict[str, Any]] = {}
    for row in task.get("acVerification") or []:
        if isinstance(row, dict):
            key = coerce_task_text(row.get("criterion")).strip()
            if key:
                prior[key] = row

    rows: List[Dict[str, Any]] = []
    for i, criterion in enumerate(acs):
        key = criterion.strip()
        old = prior.get(key, {})
        met: Optional[bool] = old.get("met")
        if i < len(checks):
            met = checks[i]
        rows.append(
            {
                "criterion": key,
                "expected": coerce_task_text(old.get("expected") or key).strip() or key,
                "actual": coerce_task_text(old.get("actual") or "").strip(),
                "met": met if met is None else bool(met),
                "updatedAt": old.get("updatedAt") or "",
            }
        )
    task["acVerification"] = rows


def update_ac_verification_from_checklist(task: Dict[str, Any], *, note: str = "") -> None:
    sync_ac_verification(task)
    checks = list(task.get("acChecklist") or [])
    ts = _now()
    for i, row in enumerate(task.get("acVerification") or []):
        if not isinstance(row, dict):
            continue
        if i < len(checks):
            row["met"] = bool(checks[i])
            row["updatedAt"] = ts
            if note and checks[i]:
                prev = coerce_task_text(row.get("actual")).strip()
                row["actual"] = _truncate(f"{prev}\n{note}".strip() if prev else note, 400)
    refresh_actual_summary(task)


def update_ac_verification_from_qa(
    task: Dict[str, Any],
    *,
    passed: bool,
    commands: Optional[List[str]] = None,
    failure_reason: str = "",
) -> None:
    sync_ac_verification(task)
    ts = _now()
    cmd_text = ", ".join(commands or [])[:200]
    snippet = f"QA playbook {'passed' if passed else 'failed'}"
    if cmd_text:
        snippet += f" ({cmd_text})"
    if failure_reason:
        snippet += f": {failure_reason[:200]}"
    for row in task.get("acVerification") or []:
        if not isinstance(row, dict):
            continue
        if passed:
            row["met"] = True
        elif row.get("met") is not True:
            row["met"] = False
        row["updatedAt"] = ts
        prev = coerce_task_text(row.get("actual")).strip()
        row["actual"] = _truncate(f"{prev}\n{snippet}".strip() if prev else snippet, 400)
    refresh_actual_summary(task, qa_passed=passed, failure_reason=failure_reason)


def refresh_actual_summary(
    task: Dict[str, Any],
    *,
    qa_passed: Optional[bool] = None,
    failure_reason: str = "",
) -> None:
    rows = [r for r in (task.get("acVerification") or []) if isinstance(r, dict)]
    met = sum(1 for r in rows if r.get("met") is True)
    total = len(rows)
    parts: List[str] = []
    if total:
        parts.append(f"Acceptance: {met}/{total} criteria marked met.")
    qf = task.get("qaFailure")
    if isinstance(qf, dict) and qf.get("reason"):
        parts.append(f"QA failure: {coerce_task_text(qf.get('reason'))[:300]}")
    elif failure_reason:
        parts.append(f"QA: {failure_reason[:300]}")
    if qa_passed is not None:
        parts.append(f"Playbook: {'passed' if qa_passed else 'failed'}")
    evidence = task.get("qaEvidence")
    if isinstance(evidence, dict) and evidence.get("playbookRun"):
        parts.append(
            "Playbook: "
            + ("passed" if evidence.get("passed") else "failed or incomplete")
        )
    for decision in reversed(task.get("decisions") or []):
        if not isinstance(decision, dict):
            continue
        if str(decision.get("type") or "") in ("completion", "qa", "qa_fail", "move"):
            summary = coerce_task_text(decision.get("summary")).strip()
            if summary:
                parts.append(summary[:300])
                break
    if qa_passed is True and not parts:
        parts.append("QA verification passed.")
    task["actualSummary"] = _truncate("\n".join(parts), 1200)


def sync_card_delivery_fields(task: Dict[str, Any], *, rebuild_expected: bool = False) -> None:
    """Rebuild expected summary and AC verification rows from spec fields."""
    if rebuild_expected or not coerce_task_text(task.get("expectedSummary")).strip():
        task["expectedSummary"] = build_expected_summary(task)
    sync_ac_verification(task)
    if coerce_task_text(task.get("actualSummary")).strip():
        refresh_actual_summary(task)
    else:
        task.setdefault("actualSummary", "")
