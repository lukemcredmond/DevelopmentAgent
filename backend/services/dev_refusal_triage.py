"""Cursor-like triage when Dev steps fail on model refusals or text-only loops."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, Optional

from backend import state
from backend.agents.task_context import find_task_by_id, get_task_lane
from backend.services.logs import add_system_log
from backend.services.needs_user_guard import clear_needs_user_reason_hash, current_sprint_step
from backend.services.sprint_speed_gates import latch_needs_po_auto_skip, record_consecutive_bad_exit

REFUSAL_TRIAGE_EXITS = frozenset(
    {
        "text_rejection_loop",
        "safety_refusal",
        "meta_refusal",
        "dev_step_wall",
    }
)


def classify_dev_text_refusal(content: str) -> str:
    """Classify assistant prose refusals for fast-stop and triage."""
    from backend.agents.scrum_agent import ScrumAgent

    if ScrumAgent._is_safety_refusal(content):
        return "safety_refusal"
    lower = str(content or "").strip().lower()
    if not lower:
        return "none"
    meta_markers = (
        "can't complete",
        "cannot complete",
        "cannot proceed",
        "can't proceed",
        "unable to proceed",
        "reached a state",
        "cannot proceed with the required",
        "without further guidance",
        "current approach is blocked",
        "repeated failures in applying",
        "repeated failures in a",
    )
    if any(m in lower for m in meta_markers):
        return "meta_refusal"
    return "none"


def fast_stop_refusal_class(refusal_class: str) -> bool:
    return refusal_class in ("safety_refusal", "meta_refusal")

AUTHORIZED_CODEGEN_SYSTEM = (
    "=== AUTHORIZED CODEGEN ===\n"
    "This workspace is the user's local software project. Implement the task using "
    "read_file, apply_patch, and write_file. Benign engineering work must not be refused."
)


def policy_refusal_defer_recommended(task: Dict[str, Any]) -> bool:
    """Defer instead of Needs User when the model is reacting to tool-policy churn."""
    if not isinstance(task, dict):
        return False
    if task.get("forcePatchNextDevStep"):
        return True
    if int(task.get("cardToolFailures") or task.get("identicalPatchFailCount") or 0) >= 2:
        return True
    if int(task.get("devStepCount") or 0) >= 8:
        return True
    return False


def maybe_defer_dev_on_policy_refusal(
    task_id: str,
    task: Dict[str, Any],
    *,
    content: str,
    exit_reason: str = "meta_refusal",
) -> Optional[str]:
    """Pick another card this sprint — avoid Needs User ↔ In Progress churn."""
    if not policy_refusal_defer_recommended(task):
        return None
    diagnosis = diagnose_dev_refusal(task, exit_reason=exit_reason, agent_result=content)
    if defer_dev_card(task_id, diagnosis):
        return (
            "Stopped: model meta refusal on a latched card — deferred to pick another card "
            "(tool-policy churn)."
        )
    return None


def dev_deferred_active(task: Dict[str, Any]) -> bool:
    until = task.get("devDeferredUntilStep")
    if until is None:
        return False
    try:
        return current_sprint_step() < int(until)
    except (TypeError, ValueError):
        return False


def _resolve_target_path(task: Dict[str, Any]) -> str:
    try:
        from backend.services.file_blocker import resolve_dev_edit_target_path

        return str(resolve_dev_edit_target_path(task) or "").strip()
    except Exception:
        return ""


def _refusal_snippet(task: Dict[str, Any], agent_result: Optional[str]) -> str:
    text = str(agent_result or "").strip()
    if text:
        return text[:200]
    outcome = task.get("lastStepOutcome") or {}
    if isinstance(outcome, dict):
        return str(outcome.get("agentResult") or outcome.get("summary") or "")[:200]
    return ""


def _backup_blocked(task: Dict[str, Any]) -> bool:
    try:
        from backend.services.backup_model import backup_model, dev_backup_model_allowed

        b = backup_model("dev")
        if not b:
            return True
        allowed, _block = dev_backup_model_allowed(b)
        return not allowed
    except Exception:
        return False


def diagnose_dev_refusal(
    task: Dict[str, Any],
    *,
    exit_reason: str,
    agent_result: Optional[str] = None,
) -> Dict[str, Any]:
    """Heuristic diagnosis without an extra LLM call."""
    reason = str(exit_reason or "").strip().lower()
    snippet = _refusal_snippet(task, agent_result)
    lower_snippet = snippet.lower()
    refusal_class = classify_dev_text_refusal(snippet)
    if reason == "meta_refusal":
        refusal_class = "meta_refusal"
    elif reason == "safety_refusal":
        refusal_class = "safety_refusal"
    safety = refusal_class == "safety_refusal"
    meta = refusal_class == "meta_refusal"

    tool_policy_deadlock = False
    if meta or safety:
        for entry in reversed(task.get("transcript") or []):
            if not isinstance(entry, dict):
                continue
            out = str(entry.get("toolOutput") or entry.get("content") or "").lower()
            if "write_file blocked" in out or "forced patch" in out:
                tool_policy_deadlock = True
                break

    ac = task.get("acceptanceCriteria") or []
    ac_count = len(ac) if isinstance(ac, list) else 0
    spec_thin = not (
        str(task.get("description") or "").strip()
        and str(task.get("scope") or "").strip()
        and str(task.get("testPlan") or "").strip()
    )
    target = _resolve_target_path(task)
    missing_target = not target

    cause = "generic_text_only"
    recommended = "defer_pick_next_card"
    if tool_policy_deadlock and (meta or safety):
        cause = "tool_policy_deadlock"
        recommended = "defer_pick_next_card"
        try:
            from backend.services.step_diagnostics import get_active_trace, log_event

            log_event("tool_policy_deadlock_detected", cause[:120])
            trace = get_active_trace()
            if trace:
                trace.note_tool_policy_deadlock(cause)
        except Exception:
            pass
    elif safety:
        cause = "model_safety_refusal"
        recommended = "defer_pick_next_card"
    elif meta:
        cause = "model_meta_refusal"
        recommended = "defer_pick_next_card"
    elif missing_target:
        cause = "missing_edit_target"
        recommended = "route_needs_po_split"
    elif spec_thin or ac_count == 0:
        cause = "vague_spec"
        recommended = "route_needs_po_enrich"
    elif _backup_blocked(task):
        cause = "backup_not_coder"
        recommended = "defer_pick_next_card"

    log_line = (
        f"{task.get('title', task.get('id'))}: {cause} — {recommended} "
        f"(exit={reason})"
    )
    return {
        "cause": cause,
        "recommendedAction": recommended,
        "logLine": log_line,
        "exitReason": reason or refusal_class or "text_rejection_loop",
        "refusalClass": refusal_class,
        "safetyRefusal": safety,
        "metaRefusal": meta,
        "toolPolicyDeadlock": tool_policy_deadlock,
        "missingTarget": missing_target,
        "specThin": spec_thin,
        "backupBlocked": _backup_blocked(task),
        "refusalSnippet": snippet[:120],
    }


def defer_dev_card(task_id: str, diagnosis: Dict[str, Any]) -> bool:
    """Stop churn on this card; prefer another runnable card this sprint."""
    from backend.services.board_service import move_board_stage
    from backend.services.sprint_service import increment_po_round_trips, _escalate_po_limit

    live = find_task_by_id(task_id)
    if not live:
        return False

    clear_needs_user_reason_hash(live)
    exit_r = str(diagnosis.get("exitReason") or "text_rejection_loop")
    if diagnosis.get("metaRefusal"):
        exit_r = "meta_refusal"
    elif diagnosis.get("safetyRefusal"):
        exit_r = "safety_refusal"
    record_consecutive_bad_exit(live, exit_r)

    step = current_sprint_step()
    live["devDeferredUntilStep"] = step + 5
    live["lastRefusalDiagnosis"] = {
        k: diagnosis.get(k)
        for k in (
            "cause",
            "recommendedAction",
            "exitReason",
            "refusalSnippet",
        )
    }

    action = str(diagnosis.get("recommendedAction") or "")
    title = str(live.get("title") or task_id)
    if action in ("route_needs_po_enrich", "route_needs_po_split") and not _escalate_po_limit(
        live
    ):
        po_msg = (
            f"Developer could not implement '{title}' — "
            f"{diagnosis.get('cause')}. Split or add file-level acceptance criteria."
        )
        increment_po_round_trips(task_id)
        move_board_stage(task_id, "Needs PO")
        latch_needs_po_auto_skip(live, reason=po_msg[:300])
        add_system_log(
            "System",
            "warning",
            f"{task_id}: refusal triage → Needs PO ({diagnosis.get('cause')})",
        )
    else:
        latch_needs_po_auto_skip(
            live,
            reason=str(diagnosis.get("logLine") or "dev refusal deferred")[:300],
        )
        add_system_log(
            "System",
            "warning",
            f"{task_id}: refusal triage — deferred to pick another card ({diagnosis.get('cause')})",
        )

    try:
        from backend.services.step_diagnostics import log_event

        log_event(
            "dev_refusal_deferred",
            json.dumps(
                {
                    "taskId": task_id,
                    "cause": diagnosis.get("cause"),
                    "action": diagnosis.get("recommendedAction"),
                },
                ensure_ascii=True,
            )[:400],
        )
    except Exception:
        pass
    return True


def defer_dev_card_on_park_blocked(
    task_id: str,
    task: Dict[str, Any],
    msg: str,
    *,
    safety_refusal: bool = False,
) -> bool:
    """When Needs User park is blocked, defer instead of staying on a hot In Progress card."""
    exit_r = "safety_refusal" if safety_refusal else "text_rejection_loop"
    rc = classify_dev_text_refusal(msg)
    if rc == "meta_refusal":
        exit_r = "meta_refusal"
    elif rc == "safety_refusal" or safety_refusal:
        exit_r = "safety_refusal"
    diagnosis = diagnose_dev_refusal(
        task,
        exit_reason=exit_r,
        agent_result=msg,
    )
    diagnosis["exitReason"] = exit_r
    return defer_dev_card(task_id, diagnosis)


def maybe_run_dev_refusal_triage(
    task_id: str,
    *,
    agent_result: Optional[str] = None,
) -> bool:
    """Run after a Dev step when exit indicates refusal / text-only churn."""
    live = find_task_by_id(task_id)
    if not live:
        return False
    if dev_deferred_active(live):
        return False
    lane = get_task_lane(task_id) or ""
    if lane not in ("In Progress", "Needs PO"):
        return False

    from backend.services.sprint_speed_gates import last_step_exit_reason

    exit_reason = last_step_exit_reason(live)
    if not exit_reason:
        from backend.services.step_diagnostics import derive_exit_reason, get_active_trace

        trace = get_active_trace()
        tools = set(trace.tools_used) if trace else set()
        exit_reason = derive_exit_reason(
            agent_result=agent_result,
            tools_used=tools,
            lane_before=lane,
            lane_after=lane,
        ).lower()

    if getattr(state, "DEV_STEP_INTRASTEP_WALL", False):
        exit_reason = "dev_step_wall"

    if exit_reason not in REFUSAL_TRIAGE_EXITS:
        return False

    diagnosis = diagnose_dev_refusal(
        live,
        exit_reason=exit_reason,
        agent_result=agent_result,
    )
    try:
        from backend.services.step_diagnostics import log_event

        log_event(
            "refusal_diagnosis",
            json.dumps(diagnosis, ensure_ascii=True)[:400],
        )
    except Exception:
        pass
    add_system_log("System", "info", diagnosis.get("logLine", "refusal triage"))
    return defer_dev_card(task_id, diagnosis)
