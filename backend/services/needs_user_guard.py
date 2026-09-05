"""Guardrails for Needs User lane escalations — dedup, cooldown, clarification routing."""

from __future__ import annotations

import datetime
import difflib
import hashlib
import re
from typing import Any, Dict, List, Optional, Tuple

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


def is_import_check_shaped(msg: str) -> bool:
    """Questions about imports/packages that dev should resolve via tools."""
    lower = str(msg or "").lower()
    patterns = (
        r"\bis .+ (imported|installed|available)\b",
        r"\bcorrectly imported\b",
        r"\bpackage.+(installed|present|added)\b",
        r"\bdo you have .+ installed\b",
        r"\bis the import (correct|valid)\b",
    )
    return any(re.search(p, lower) for p in patterns)


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
    """Explicit PO-routing signals only — avoid substring false positives in long dev output."""
    if dev_explicit_needs_user(result):
        return False
    lower = result.lower()
    explicit_markers = (
        "escalate to po",
        "move the task to 'needs po'",
        "moving to needs po",
        "move to needs po",
        "escalating to product owner",
    )
    if any(m in lower for m in explicit_markers):
        return True
    for line in lower.split("\n"):
        stripped = line.strip()
        if stripped.startswith("needs po:") or stripped.startswith("need po:"):
            return True
        if stripped.startswith("needs clarification:") or stripped.startswith("need clarification:"):
            return True
        if stripped.startswith("blocked on requirements:"):
            return True
    return False


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
    task_id = str(task.get("id") or "")
    if task_id:
        try:
            from backend.services.task_qa_markdown import update_task_qa_markdown

            update_task_qa_markdown(task_id)
        except Exception:
            pass


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

    if is_import_check_shaped(text) and not dev_explicit_needs_user(text):
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


_GENERIC_NEEDS_USER_SNIPPETS = (
    "could not agree after",
    "please clarify requirements",
    "agents made no progress",
    "please clarify requirements or make a decision",
    "review the task and provide a decision",
    "agent requires your input",
    "missing information or decision needed",
)

_SECRET_SNIPPETS = (
    "api key",
    "apikey",
    "secret",
    "password",
    "credential",
    "oauth",
    "access token",
    "auth token",
    "private key",
)

_QUESTION_STARTERS = (
    "which ",
    "what ",
    "should we",
    "do you want",
    "pick ",
    "choose ",
    "confirm ",
    "provide ",
    "paste ",
)


def looks_generic_needs_user_text(text: str) -> bool:
    lower = str(text or "").strip().lower()
    if not lower:
        return True
    return any(p in lower for p in _GENERIC_NEEDS_USER_SNIPPETS)


def _looks_secret_ask(text: str) -> bool:
    lower = str(text or "").lower()
    return any(p in lower for p in _SECRET_SNIPPETS)


def _looks_specific_question(text: str) -> bool:
    raw = str(text or "").strip()
    if len(raw) < 12 or looks_generic_needs_user_text(raw):
        return False
    lower = raw.lower()
    if "?" in raw:
        return True
    if _looks_secret_ask(raw):
        return True
    return any(lower.startswith(s) or f" {s}" in f" {lower}" for s in _QUESTION_STARTERS)


def _question_from_raw_msg(raw: str) -> str:
    text = str(raw or "").strip()
    if not text:
        return ""
    for line in text.splitlines():
        stripped = line.strip()
        low = stripped.lower()
        for prefix in ("userquestion:", "user question:", "needs user:", "need user:"):
            if low.startswith(prefix):
                rest = stripped.split(":", 1)[1].strip()
                if _looks_specific_question(rest):
                    return rest
    if _looks_specific_question(text):
        para = text.split("\n\n", 1)[0].strip()
        return para[:500]
    return ""


def _last_failed_tool(task: Dict[str, Any]) -> str:
    for entry in reversed(task.get("transcript") or []):
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("toolName") or "").strip()
        content = str(entry.get("content") or "")
        failed = entry.get("toolSuccess") is False or (
            name and ("fail" in content.lower() or "error" in content.lower() or "✗" in content)
        )
        if failed and name:
            snippet = " ".join(content.split())[:120]
            return f"{name}: {snippet}" if snippet else name
    return ""


def _first_lint_line(task: Dict[str, Any]) -> str:
    diagnostics = task.get("lastCommandDiagnostics") or []
    if not isinstance(diagnostics, list) or not diagnostics:
        return ""
    first = diagnostics[0]
    if not isinstance(first, dict):
        return str(first)[:160]
    loc = f"{first.get('file', '?')}:{first.get('line', '?')}"
    return f"{loc} — {str(first.get('message') or '')[:140]}"


def _spec_gap_lines(task: Dict[str, Any]) -> List[str]:
    gaps: List[str] = []
    desc = str(task.get("description") or "").strip()
    ac = task.get("acceptanceCriteria") or []
    if not isinstance(ac, list):
        ac = []
    ac_usable = [str(x).strip() for x in ac if str(x).strip()]
    if len(desc) < 40:
        gaps.append("the description is too vague to implement")
    if not ac_usable:
        gaps.append("acceptance criteria are empty")
    return gaps


def _exit_reason(task: Dict[str, Any]) -> str:
    outcome = task.get("lastStepOutcome") or {}
    if isinstance(outcome, dict):
        return str(
            outcome.get("exitReason") or outcome.get("stopReason") or ""
        ).strip().lower()
    return str(task.get("lastCircuitExitReason") or "").strip().lower()


def _resolve_needs_user_kind(task: Dict[str, Any], kind: str, raw_msg: str) -> str:
    raw = str(raw_msg or "")
    if _looks_secret_ask(raw) or _looks_secret_ask(str(task.get("userQuestion") or "")):
        return "secret"
    if stuck_is_tool_or_lint(task):
        return "lint"
    exit_r = _exit_reason(task)
    if exit_r == "explore_budget_exhausted" or task.get("forcePatchNextDevStep"):
        return "explore"
    if exit_r == "patch_budget_exhausted":
        return "patch"
    if kind == "po_limit":
        return "po_limit"
    if kind == "dev_board_move" and _looks_specific_question(raw):
        return "product_choice"
    if kind == "dev_escalation" and int(task.get("poRoundTrips") or 0):
        return "po_limit"
    if _looks_specific_question(raw):
        return "product_choice"
    if kind:
        return "stuck" if kind in ("stuck_loop", "clarification") else kind
    return "stuck"


def build_needs_user_brief(
    task: Dict[str, Any],
    *,
    kind: str = "stuck_loop",
    raw_msg: str = "",
) -> Dict[str, str]:
    """Structured Needs User copy: question, why, action, suggested resolve target.

    Does not call an LLM — uses diagnosis, lint, last step outcome, and spec gaps.
    """
    title = str(task.get("title") or task.get("id") or "this card")
    resolved_kind = _resolve_needs_user_kind(task, kind, raw_msg)
    diagnosis = task.get("lastDiagnosis") if isinstance(task.get("lastDiagnosis"), dict) else {}
    problem = str((diagnosis or {}).get("problem") or "").strip()
    recommended = str((diagnosis or {}).get("recommendedAction") or "").strip()
    lint_line = _first_lint_line(task)
    failed_tool = _last_failed_tool(task)
    outcome = task.get("lastStepOutcome") if isinstance(task.get("lastStepOutcome"), dict) else {}
    why_stayed = str((outcome or {}).get("whyCardStayed") or "").strip()
    exit_r = _exit_reason(task)
    qa_fail = task.get("qaFailure") if isinstance(task.get("qaFailure"), dict) else {}
    qa_reason = str((qa_fail or {}).get("reason") or "").strip()
    gaps = _spec_gap_lines(task)
    specific = _question_from_raw_msg(raw_msg)

    question = specific
    why_parts: List[str] = []
    action = ""
    suggested = "dev"

    if resolved_kind == "secret":
        question = question or (
            "Which secret/credential should Developer use (paste the value, or name the env var)?"
        )
        why_parts.append("The agent cannot invent credentials or API keys.")
        action = (
            "Paste the secret, or write e.g. 'use env OPENAI_API_KEY and do not commit it'. "
            "Then click Send to Developer."
        )
    elif resolved_kind == "lint":
        question = question or (
            "Which lint/tool error should Developer fix or ignore first"
            + (f" ({lint_line})?" if lint_line else "?")
        )
        if lint_line:
            why_parts.append(f"Blocked on a lint/tool error: {lint_line}.")
        else:
            why_parts.append("Blocked on lint or tool failures, not a missing product decision.")
        if failed_tool:
            why_parts.append(f"Last failed tool: {failed_tool}")
        action = (
            "You cannot unblock this by sending it back to Product Owner. "
            "Reply with 'fix {file}' or 'ignore {rule} and continue', then click Send to Developer."
        )
    elif resolved_kind == "explore":
        question = question or (
            f'Which file or function should Developer change first to implement "{title}"?'
        )
        why_parts.append(
            "Developer used the explore budget (read/list/search) and never called apply_patch/write_file."
        )
        if why_stayed:
            why_parts.append(why_stayed[:280])
        action = (
            "Name the starting file/function, or say 'split this card'. "
            "Then click Send to Developer (not Product Owner)."
        )
    elif resolved_kind == "patch":
        question = question or (
            f'How should Developer apply the change for "{title}" after patch attempts failed?'
        )
        why_parts.append("Patch/write attempts exhausted without a successful file edit.")
        if failed_tool:
            why_parts.append(f"Last failed tool: {failed_tool}")
        action = (
            "Describe the intended edit (path + what to change), or say 'split the card'. "
            "Then click Send to Developer."
        )
    elif resolved_kind == "po_limit":
        if not question:
            if gaps and "acceptance criteria are empty" in gaps:
                question = (
                    f'What are the acceptance criteria for "{title}"? '
                    "List 2–5 done-when bullets."
                )
            elif gaps:
                question = (
                    f'What should "{title}" do, in one paragraph, including user-visible behavior?'
                )
            elif problem:
                question = f"How should we resolve: {problem[:220]}?"
            else:
                question = (
                    f'What is the one product decision Developer is missing for "{title}"?'
                )
        if gaps:
            why_parts.append("The spec is still incomplete: " + "; ".join(gaps) + ".")
        if problem:
            why_parts.append(problem[:240])
        trips = int(task.get("poRoundTrips") or 0)
        if trips:
            why_parts.append(
                f"Product Owner already reviewed this {trips} time(s); another PO bounce will not unblock it."
            )
        action = (
            "Answer the question above, then click Send to Developer. "
            "Only click Send to Product Owner if you are rewriting acceptance criteria."
        )
    else:
        if not question:
            if problem:
                question = f"How should we resolve: {problem[:220]}?"
            elif qa_reason:
                question = f"QA failed — how should we treat this: {qa_reason[:180]}?"
            elif lint_line:
                question = f"What should Developer do about: {lint_line}?"
            elif gaps:
                question = f'Fill the spec gap for "{title}": {gaps[0]}.'
            else:
                question = (
                    f'What decision or missing fact does Developer need to continue "{title}"?'
                )
        if why_stayed:
            why_parts.append(why_stayed[:280])
        elif exit_r:
            why_parts.append(f"Last step ended with {exit_r}.")
        action = (
            "Answer in one short message, then click Send to Developer to resume implementation. "
            "Use Send to Product Owner only to rewrite the spec."
        )

    if recommended and recommended not in (action + question):
        why_parts.append(f"Suggested next step from diagnosis: {recommended[:200]}")
    if qa_reason and resolved_kind != "secret" and qa_reason not in " ".join(why_parts):
        why_parts.append(f"QA failure: {qa_reason[:160]}")
    if not why_parts:
        why_parts.append("The sprint stopped because the agents cannot proceed without your answer.")

    why = " ".join(why_parts)
    if (
        recommended
        and resolved_kind in ("po_limit", "stuck")
        and not looks_generic_needs_user_text(recommended)
    ):
        if "send to" not in recommended.lower():
            action = f"{recommended.rstrip('.')} Then click Send to Developer."
        else:
            action = recommended

    return {
        "kind": resolved_kind,
        "question": question[:500],
        "why": why[:600],
        "action": action[:600],
        "suggestedTarget": suggested,
    }


def apply_needs_user_brief(task: Dict[str, Any], brief: Dict[str, str]) -> None:
    """Write structured Needs User fields onto the task."""
    task["userQuestion"] = str(brief.get("question") or "")[:500]
    task["needsUserReason"] = str(brief.get("why") or "")[:600]
    task["needsUserAction"] = str(brief.get("action") or "")[:600]
    task["needsUserKind"] = str(brief.get("kind") or "")[:40]
    target = str(brief.get("suggestedTarget") or "dev").strip().lower()
    if target not in ("dev", "po", "refinement"):
        target = "dev"
    task["needsUserSuggestedTarget"] = target


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
