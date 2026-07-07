"""Guardrails for Needs User lane escalations — dedup, cooldown, clarification routing."""

from __future__ import annotations

import datetime
import difflib
import hashlib
import re
from typing import Any, Dict, Optional, Tuple

from backend import state
from backend.services.workflow_settings import get_workflow_settings

NEEDS_USER_EXPLICIT_MARKERS = (
    "move the task to 'needs user'",
    "moving to needs user",
    "move to needs user",
    "userquestion:",
    "needs user:",
    "requires user input:",
    "escalate to user",
)

CLARIFICATION_PHRASES = (
    "clarify requirements",
    "please clarify",
    "unclear requirement",
    "which approach",
    "could you confirm",
    "agents made no progress",
    "could not agree",
)


def normalize_question(text: str) -> str:
    t = re.sub(r"\s+", " ", str(text or "").lower().strip())
    return t[:500]


def question_similarity(a: str, b: str) -> float:
    na, nb = normalize_question(a), normalize_question(b)
    if not na or not nb:
        return 0.0
    return difflib.SequenceMatcher(None, na, nb).ratio()


def is_clarification_shaped(msg: str) -> bool:
    lower = str(msg or "").lower()
    if any(p in lower for p in CLARIFICATION_PHRASES):
        return True
    if "requirements" in lower and ("clarify" in lower or "unclear" in lower):
        return True
    return False


def dev_explicit_needs_user(result: str) -> bool:
    """True only when the agent explicitly escalates to Needs User."""
    lower = result.lower()
    if any(m in lower for m in NEEDS_USER_EXPLICIT_MARKERS):
        return True
    for line in lower.split("\n"):
        stripped = line.strip()
        if stripped.startswith("needs user:") or stripped.startswith("need user:"):
            return True
        if stripped.startswith("user decision:"):
            return True
    return False


def dev_clarification_from_result(result: str) -> bool:
    """Clarification signals that should route to Needs PO instead."""
    if dev_explicit_needs_user(result):
        return False
    lower = result.lower()
    loose = (
        "needs clarification",
        "need clarification",
        "unclear requirement",
        "move to needs po",
        "escalate to po",
    )
    return any(m in lower for m in loose) or is_clarification_shaped(result)


def prefer_po_instruction_suffix() -> str:
    return (
        " Prefer Needs PO over Needs User for requirement clarification. "
        "Needs User is only for secrets, credentials, irreversible external actions, "
        "or product choices with no reasonable default in the brief or acceptance criteria. "
        "Do NOT move to Needs User for lint errors, missing files, or vague implementation questions."
    )


def current_sprint_step() -> int:
    return int(state.SPRINT_PROGRESS_STEP or 0)


def set_needs_user_cooldown(task: Dict[str, Any], steps: Optional[int] = None) -> None:
    ws = get_workflow_settings()
    n = steps if steps is not None else int(ws.get("needsUserCooldownSteps", 3))
    task["needsUserCooldownUntilStep"] = current_sprint_step() + n


def cooldown_active(task: Dict[str, Any]) -> bool:
    until = task.get("needsUserCooldownUntilStep")
    if until is None:
        return False
    return current_sprint_step() < int(until)


def reason_hash(msg: str) -> str:
    return hashlib.sha256(normalize_question(msg).encode()).hexdigest()[:16]


def append_user_resolution(
    task: Dict[str, Any],
    question: str,
    answer: str,
    target_lane: str,
) -> None:
    resolutions = task.get("userResolutions")
    if not isinstance(resolutions, list):
        resolutions = []
        task["userResolutions"] = resolutions
    resolutions.append(
        {
            "question": str(question or "")[:500],
            "answer": str(answer or "")[:2000],
            "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "targetLane": target_lane,
        }
    )
    if len(resolutions) > 20:
        task["userResolutions"] = resolutions[-20:]


def should_escalate_to_needs_user(
    task: Dict[str, Any],
    msg: str,
) -> Tuple[bool, str]:
    """Return (allowed, block_reason). block_reason is empty when allowed."""
    text = str(msg or "").strip()
    if not text:
        return False, "empty_question"

    if cooldown_active(task):
        return False, "cooldown_active"

    for res in task.get("userResolutions") or []:
        if not isinstance(res, dict):
            continue
        q = str(res.get("question") or "")
        if question_similarity(text, q) >= 0.85:
            task["needsUserDuplicate"] = True
            return False, "duplicate_question"

    current_reason = task.get("needsUserReason") or task.get("userQuestion") or ""
    if current_reason and question_similarity(text, current_reason) >= 0.85:
        from backend.agents.task_context import get_task_lane

        if get_task_lane(str(task.get("id", ""))) == "Needs User":
            task["needsUserDuplicate"] = True
            return False, "already_in_needs_user"

    last_hash = task.get("lastNeedsUserReasonHash")
    h = reason_hash(text)
    if last_hash and last_hash == h:
        task["needsUserDuplicate"] = True
        return False, "same_reason_hash"

    if is_clarification_shaped(text) and not dev_explicit_needs_user(text):
        return False, "clarification_use_po"

    task["needsUserDuplicate"] = False
    task["lastNeedsUserReasonHash"] = h
    return True, ""


def stuck_is_tool_or_lint(task: Dict[str, Any]) -> bool:
    """True when stuck state is likely from lint/tool failures, not user decisions."""
    diagnostics = task.get("lastCommandDiagnostics") or []
    if isinstance(diagnostics, list) and len(diagnostics) > 0:
        return True
    for entry in reversed(task.get("transcript") or []):
        if not isinstance(entry, dict):
            continue
        if entry.get("toolSuccess") is False:
            return True
        content = str(entry.get("content") or "").lower()
        if entry.get("toolName") and ("fail" in content or "error" in content):
            return True
    qa_fail = task.get("qaFailure")
    if isinstance(qa_fail, dict) and qa_fail.get("reason"):
        return True
    return False


def build_stuck_escalation_message(task: Dict[str, Any], lane: str, max_stuck: int) -> str:
    """Concrete stuck message from diagnosis or lint metadata when available."""
    ld = task.get("lastDiagnosis")
    if isinstance(ld, dict) and ld.get("problem"):
        return (
            f"No progress after {max_stuck} steps in '{lane}'. "
            f"Blocker: {ld.get('problem', '')[:200]}. "
            f"Suggested action: {ld.get('recommendedAction', 'Review and unblock')[:200]}"
        )
    diagnostics = task.get("lastCommandDiagnostics") or []
    if isinstance(diagnostics, list) and diagnostics:
        first = diagnostics[0]
        if isinstance(first, dict):
            loc = f"{first.get('file', '?')}:{first.get('line', '?')}"
            return (
                f"No progress after {max_stuck} steps in '{lane}' — "
                f"lint/tool blocker at {loc}: {str(first.get('message', ''))[:120]}"
            )
    return (
        f"Agents made no progress after {max_stuck} steps in '{lane}'. "
        "Please clarify requirements or make a decision."
    )
