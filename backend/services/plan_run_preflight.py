"""Pre-flight checks before Plan & Run / auto-sprint."""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any, Dict, List, Optional

from backend import state
from backend.agents.task_context import count_claimable_backlog_tasks, normalize_task
from backend.services.plan_run_orchestration import brief_is_actionable, has_dev_ready_backlog
from backend.services.workflow_settings import get_workflow_settings


@dataclass
class PreflightIssue:
    code: str
    severity: str  # fail | warn
    message: str
    fix: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _max_ac_for_card(ws: Dict[str, Any]) -> int:
    try:
        return max(1, int(ws.get("splitCardWhenAcOver") or 3))
    except (TypeError, ValueError):
        return 3


def _scan_backlog_cards(ws: Dict[str, Any]) -> List[PreflightIssue]:
    issues: List[PreflightIssue] = []
    max_ac = _max_ac_for_card(ws)
    for lane in ("Backlog", "Features", "In Progress"):
        for task in state.SHARED_BOARD.get(lane, []) or []:
            if not isinstance(task, dict):
                continue
            normalize_task(task)
            if not task.get("requiresDev", True) or task.get("workType") == "planning":
                if lane == "Backlog" and ws.get("planRunExecutionProfile", "implementer") == "implementer":
                    issues.append(
                        PreflightIssue(
                            "planning_only_card",
                            "warn",
                            f"Card '{task.get('title')}' is planning-only — Dev will skip it.",
                            "Split into implementation children with requiresDev: true.",
                        )
                    )
                continue
            ac = task.get("acceptanceCriteria") or []
            if not ac:
                issues.append(
                    PreflightIssue(
                        "missing_ac",
                        "fail",
                        f"Card '{task.get('title') or task.get('id')}' has no acceptance criteria.",
                        "Add ≤3 testable AC lines or re-run PO planning.",
                    )
                )
            elif len(ac) > max_ac:
                issues.append(
                    PreflightIssue(
                        "oversized_ac",
                        "warn",
                        f"Card '{task.get('title')}' has {len(ac)} AC (>{max_ac}).",
                        "Split the card before auto-sprint.",
                    )
                )
    return issues


def validate_plan_run_preflight(
    brief: str,
    *,
    ws: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    if ws is None:
        ws = get_workflow_settings()
    issues: List[PreflightIssue] = []

    from backend.services.simulation_gate import has_pending_simulation

    if has_pending_simulation():
        issues.append(
            PreflightIssue(
                "simulation_pending",
                "fail",
                "Simulation fallback is pending confirmation.",
                "Confirm or cancel the simulation gate in the UI.",
            )
        )

    try:
        from backend.services.llm_provider import get_chat_provider

        health = get_chat_provider().health()
        if not health.ok:
            issues.append(
                PreflightIssue(
                    "llm_unreachable",
                    "fail",
                    f"LLM endpoint unreachable: {health.error or health.url}",
                    "Start Ollama/LM Studio or fix llmBaseUrl in Workflow settings.",
                )
            )
    except Exception as exc:
        issues.append(
            PreflightIssue(
                "llm_unreachable",
                "fail",
                f"Could not reach LLM provider: {exc}",
                "Check Workflow → Models.",
            )
        )

    if ws.get("enableSemanticSprintContext", True) and ws.get("enableSemanticSearch", True):
        try:
            from backend.storage.code_index import CodeIndexEngine

            status = CodeIndexEngine().index_status()
            if not status.get("ok"):
                issues.append(
                    PreflightIssue(
                        "qdrant_down",
                        "warn",
                        "Semantic index unavailable — Dev will rely on grep/read only.",
                        "Start Qdrant and Reindex codebase in Workflow settings.",
                    )
                )
            elif int(status.get("chunks") or 0) <= 0:
                issues.append(
                    PreflightIssue(
                        "qdrant_empty",
                        "warn",
                        "Semantic index is empty.",
                        "Run Reindex codebase before Plan & Run for better context.",
                    )
                )
        except Exception:
            issues.append(
                PreflightIssue(
                    "qdrant_unknown",
                    "warn",
                    "Could not verify Qdrant index status.",
                    "Ensure Qdrant is running if you use semantic preload.",
                )
            )

    issues.extend(_scan_backlog_cards(ws))

    if not has_dev_ready_backlog() and not brief_is_actionable(brief):
        issues.append(
            PreflightIssue(
                "no_dev_work",
                "fail",
                "No dev-ready backlog cards and brief is not actionable.",
                "Write a concrete brief (≥40 chars with implement/fix/add) or generate backlog first.",
            )
        )

    fails = [i for i in issues if i.severity == "fail"]
    return {
        "ok": len(fails) == 0,
        "blocked": len(fails) > 0,
        "claimableBacklog": count_claimable_backlog_tasks(),
        "issues": [i.to_dict() for i in issues],
    }
