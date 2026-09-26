"""Guardrails for Needs User lane escalations — dedup, cooldown, clarification routing."""

from __future__ import annotations

import datetime
import difflib
import hashlib
import re
from typing import Any, Callable, Dict, List, Optional, Tuple

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


_USER_ONLY_ESCALATION_KINDS = frozenset(
    {"phase_cycle_cap", "secret", "product_choice", "po_limit", "dev_board_move"}
)

_AUTONOMOUS_KINDS = frozenset({"lint", "explore", "patch"})

_MISROUTED_NEEDS_USER_PATTERNS = (
    "old_text not found",
    "apply_patch",
    "blocked on lint",
)


def brief_has_actionable_options(brief: Dict[str, Any]) -> bool:
    opts = brief.get("options") or []
    return isinstance(opts, list) and len(opts) >= 2


def should_park_in_needs_user(
    task: Dict[str, Any],
    brief: Dict[str, Any],
) -> Tuple[bool, str]:
    """Needs User only when there are real MCQ choices for the user."""
    kind = str(brief.get("kind") or "")
    if kind in _AUTONOMOUS_KINDS:
        if not _text_rejection_bypass_lint_blocker(
            task,
            kind="stuck_loop",
            msg=str(brief.get("question") or ""),
        ):
            return False, "autonomous_tool_blocker"
    if stuck_is_tool_or_lint(task) and kind not in (
        "secret",
        "phase_cycle_cap",
        "product_choice",
    ):
        q = str(brief.get("question") or "")
        if kind in ("stuck_loop", "stuck") and (
            _text_rejection_escalation_allowed(task)
            or _text_rejection_park_message(q)
            or _text_rejection_bypass_lint_blocker(task, kind=kind, msg=q)
        ):
            pass
        else:
            return False, "lint_use_file_blocker"
    if not brief_has_actionable_options(brief):
        return False, "no_mcq_options"
    return True, ""


def clear_needs_user_fields(task: Dict[str, Any]) -> None:
    for key in (
        "userQuestion",
        "needsUserReason",
        "needsUserAction",
        "needsUserKind",
        "needsUserSuggestedTarget",
        "needsUserOptions",
    ):
        task.pop(key, None)


def reroute_tool_blocker_to_dev(
    task_id: str,
    task: Dict[str, Any],
    reason: str,
) -> bool:
    """Keep card In Progress for autonomous Developer retry instead of Needs User."""
    clear_needs_user_fields(task)
    task["forcePatchNextDevStep"] = True
    task.pop("needsUserDuplicate", None)
    task.pop("lastNextWorkKey", None)
    task.pop("lastNextWorkNoWrite", None)
    try:
        from backend.services.logs import add_system_log

        add_system_log(
            "System",
            "info",
            f"{task_id}: staying In Progress for autonomous Dev retry — {str(reason or '')[:120]}",
        )
    except Exception:
        pass
    return True


def should_reconcile_needs_user_task(task: Dict[str, Any]) -> bool:
    if not isinstance(task, dict):
        return False
    kind = str(task.get("needsUserKind") or "")
    if kind in _AUTONOMOUS_KINDS:
        return True
    if kind in ("stuck", "phase_cycle_cap") and not task.get("needsUserOptions"):
        return True
    if not task.get("needsUserOptions") and stuck_is_tool_or_lint(task):
        return True
    blob = " ".join(
        [
            str(task.get("userQuestion") or ""),
            str(task.get("needsUserReason") or ""),
            str(task.get("needsUserAction") or ""),
        ]
    ).lower()
    if any(pattern in blob for pattern in _MISROUTED_NEEDS_USER_PATTERNS):
        return True
    brief = build_needs_user_brief(task, kind=kind or "stuck_loop")
    allowed, _ = should_park_in_needs_user(task, brief)
    return not allowed


def reconcile_needs_user_task_to_dev(
    task: Dict[str, Any],
    *,
    source: str = "reconcile",
) -> bool:
    """Move misrouted Needs User cards back to In Progress for autonomous Dev."""
    task_id = str(task.get("id") or "")
    if not task_id:
        return False
    try:
        from backend.agents.task_context import get_task_lane
    except Exception:
        get_task_lane = lambda _tid: ""  # type: ignore[assignment,misc]

    lane = get_task_lane(task_id)
    on_board_nu = lane == "Needs User"
    if not on_board_nu and str(task.get("status") or "") != "Needs User":
        return False

    clear_needs_user_fields(task)
    if is_lint_wall_card(task):
        if on_board_nu:
            from backend.services.sprint_service import _keep_lint_card_for_developer

            _keep_lint_card_for_developer(task)
        else:
            task["forcePatchNextDevStep"] = True
            task["status"] = "In Progress"
    else:
        task["forcePatchNextDevStep"] = True
        if on_board_nu:
            task["status"] = "In Progress"
            from backend.services.board_service import move_board_stage

            move_board_stage(task_id, "In Progress")
        else:
            task["status"] = "In Progress"

    try:
        from backend.services.logs import add_system_log

        add_system_log(
            "System",
            "info",
            f"{task_id}: Reconciled misrouted Needs User → In Progress ({source})",
        )
    except Exception:
        pass
    return True


def reconcile_autonomous_needs_user_cards() -> Dict[str, Any]:
    """Scan Needs User lane and reroute autonomous lint/tool/explore cards to Dev."""
    reconciled: List[str] = []
    refreshed: List[str] = []
    with state.STATE_LOCK:
        tasks = list(state.SHARED_BOARD.get("Needs User") or [])
    for task in tasks:
        if not isinstance(task, dict):
            continue
        task_id = str(task.get("id") or "")
        if should_reconcile_needs_user_task(task):
            if task_id and reconcile_needs_user_task_to_dev(task, source="autonomous"):
                reconciled.append(task_id)
            continue
        if task_id and refresh_generic_needs_user_options(task):
            refreshed.append(task_id)
    if reconciled or refreshed:
        try:
            from backend.services.project_service import save_current_project_state

            save_current_project_state()
        except Exception:
            pass
    return {"reconciled": reconciled, "refreshed": refreshed, "count": len(reconciled)}


def _text_rejection_escalation_allowed(task: Dict[str, Any]) -> bool:
    """Allow Needs User when repeated text-only refusals burned auto-sprint retries."""
    if not isinstance(task, dict):
        return False
    bad = int(task.get("consecutiveBadExits") or 0)
    if bad < 2:
        return False
    from backend.services.sprint_speed_gates import last_step_exit_reason

    exit_r = last_step_exit_reason(task)
    circuit = str(task.get("lastCircuitExitReason") or "").strip().lower()
    return exit_r == "text_rejection_loop" or circuit == "text_rejection_loop"


def _text_rejection_park_message(msg: str) -> bool:
    lower = str(msg or "").lower()
    return "text rejection loop" in lower or "refused tools" in lower or "text-only" in lower


def _text_rejection_bypass_lint_blocker(
    task: Dict[str, Any],
    *,
    kind: str,
    msg: str = "",
    safety_refusal: bool = False,
    step_text_rejections: int = 0,
) -> bool:
    """Feature-card text loops and safety refusals may park despite lint diagnostics on card."""
    if kind != "stuck_loop":
        return False
    if is_lint_wall_card(task):
        return False
    if safety_refusal or int(step_text_rejections or 0) >= 2:
        return True
    if _text_rejection_escalation_allowed(task) or _text_rejection_park_message(msg):
        return True
    if _exit_reason(task) == "text_rejection_loop":
        return True
    return False


def should_escalate_to_needs_user(
    task: Dict[str, Any],
    msg: str,
    *,
    kind: str = "",
) -> Tuple[bool, str]:
    """Return (allowed, block_reason). block_reason is empty when allowed."""
    text = str(msg or "").strip()
    if not text:
        return False, "empty_question"

    if stuck_is_tool_or_lint(task) and kind not in _USER_ONLY_ESCALATION_KINDS:
        if kind == "stuck_loop" and (
            _text_rejection_escalation_allowed(task)
            or _text_rejection_park_message(text)
            or _text_rejection_bypass_lint_blocker(task, kind=kind, msg=text)
        ):
            pass
        else:
            return False, "lint_use_file_blocker"

    latch_park = kind == "phase_cycle_cap" or bool(task.get("phaseCycleCapReached"))

    if not latch_park and cooldown_active(task):
        return False, "cooldown_active"

    if not latch_park:
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
    if last_hash and last_hash == h and not latch_park:
        task["needsUserDuplicate"] = True
        return False, "same_reason_hash"

    # Phase-cycle cap is a latch park, not a PO clarification bounce.
    if kind != "phase_cycle_cap":
        if is_clarification_shaped(text) and not dev_explicit_needs_user(text):
            return False, "clarification_use_po"

        if is_import_check_shaped(text) and not dev_explicit_needs_user(text):
            return False, "clarification_use_po"

    task["needsUserDuplicate"] = False
    task["lastNeedsUserReasonHash"] = h
    return True, ""


def is_lint_wall_card(task: Dict[str, Any]) -> bool:
    """True for lint fanout cards (Lint: title or lintSourceFile), not generic feature cards."""
    if not isinstance(task, dict):
        return False
    title = str(task.get("title") or "")
    return title.startswith("Lint: ") or bool(task.get("lintSourceFile"))


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


_FLUTTER_ANALYZE_TAIL = re.compile(
    r"(\S+\.[A-Za-z0-9_]+):(\d+)(?::(\d+))?\s*$"
)
_FLUTTER_ANALYZE_PREFIX = re.compile(r"^(?:error|warning|info)\s+[•\-]", re.I)


def _sanitize_diag_file(file_val: str, line: Any) -> Tuple[str, str]:
    """If `file` stored a whole analyze line, recover path:line."""
    raw = str(file_val or "").strip().replace("\\", "/")
    line_s = str(line if line not in (None, "") else "?")
    if not raw:
        return "?", line_s
    if _FLUTTER_ANALYZE_PREFIX.search(raw) or (raw.count("•") >= 1 and ":" in raw):
        tail = _FLUTTER_ANALYZE_TAIL.search(raw)
        if tail:
            return tail.group(1), tail.group(2)
    if len(raw) > 80 and " " in raw:
        tail = _FLUTTER_ANALYZE_TAIL.search(raw)
        if tail:
            return tail.group(1), tail.group(2)
    return raw, line_s


def _first_lint_line(task: Dict[str, Any]) -> str:
    diagnostics = task.get("lastCommandDiagnostics") or []
    if not isinstance(diagnostics, list) or not diagnostics:
        return ""
    first = diagnostics[0]
    if not isinstance(first, dict):
        return str(first)[:160]
    path, line_s = _sanitize_diag_file(str(first.get("file") or ""), first.get("line"))
    msg = str(first.get("message") or "").strip()
    if msg.startswith("error") and "•" in msg:
        inner = re.sub(r"^(?:error|warning|info)\s+[•\-]\s+", "", msg, flags=re.I)
        inner = re.split(r"\s+[•\-]\s+", inner, maxsplit=1)[0].strip()
        if inner:
            msg = inner
    loc = f"{path}:{line_s}"
    return f"{loc} — {msg[:140]}" if msg else loc


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
        reason = str(
            outcome.get("exitReason") or outcome.get("stopReason") or ""
        ).strip().lower()
        if reason:
            return reason
    diag = task.get("lastStepDiagnostics") if isinstance(task.get("lastStepDiagnostics"), dict) else {}
    if diag:
        reason = str(diag.get("exitReason") or "").strip().lower()
        if reason:
            return reason
    progress = task.get("lastStepProgress") if isinstance(task.get("lastStepProgress"), dict) else {}
    if progress:
        reason = str(progress.get("exitReason") or "").strip().lower()
        if reason:
            return reason
    return str(task.get("lastCircuitExitReason") or "").strip().lower()


def _diagnosis_from_task_evidence(task: Dict[str, Any]) -> Dict[str, Any]:
    """Deterministic problem/action from step outcome, lint, and diagnostics (no LLM)."""
    outcome = task.get("lastStepOutcome") if isinstance(task.get("lastStepOutcome"), dict) else {}
    step_diag = (
        task.get("lastStepDiagnostics") if isinstance(task.get("lastStepDiagnostics"), dict) else {}
    )
    perf = step_diag.get("performanceSummary") if isinstance(step_diag.get("performanceSummary"), dict) else {}
    bottleneck = str(perf.get("primaryBottleneck") or "").strip()
    exit_r = _exit_reason(task)
    lint_line = _first_lint_line(task)
    failed_tool = _last_failed_tool(task)
    snippet = str((outcome or {}).get("agentResultSnippet") or "").strip()
    suggested = str((outcome or {}).get("suggestedAction") or "").strip()

    try:
        from backend.services.file_blocker import resolve_dev_edit_target_path

        edit_target = str(resolve_dev_edit_target_path(task) or "").strip()
    except Exception:
        edit_target = ""

    problem_parts: List[str] = []
    if lint_line:
        problem_parts.append(lint_line)
    elif failed_tool:
        problem_parts.append(failed_tool)
    elif snippet:
        problem_parts.append(snippet[:240])
    elif exit_r:
        problem_parts.append(f"Developer step ended: {exit_r.replace('_', ' ')}")
    if bottleneck and bottleneck != "none":
        problem_parts.append(f"Bottleneck: {bottleneck.replace('_', ' ')}")

    problem = "; ".join(problem_parts)[:500]

    action_parts: List[str] = []
    if suggested and not looks_generic_needs_user_text(suggested):
        action_parts.append(suggested)
    if edit_target and lint_line:
        action_parts.append(
            f"Edit `{edit_target}` to fix the reported issue, then re-run analyze/tests."
        )
    elif edit_target and exit_r in ("read_only_no_edits", "tool_failure_stop", "patch_budget_exhausted"):
        action_parts.append(
            f"Implement the fix in `{edit_target}` with apply_patch or write_file."
        )
    if exit_r == "phase_cycle_cap":
        action_parts.append(
            "Split this card into smaller single-file tasks, or reset the Developer visit latch."
        )
    if not action_parts and problem:
        action_parts.append(f"Unblock: {problem[:200]}")

    recommended = " ".join(action_parts)[:500]
    return {
        "summary": problem[:200] if problem else "",
        "problem": problem,
        "recommendedAction": recommended,
        "suggestedAgent": "dev",
        "source": "evidence",
    }


def _ensure_evidence_diagnosis(task: Dict[str, Any]) -> None:
    """Fill lastDiagnosis from lint/step evidence when PO diagnosis is missing or generic."""
    if not isinstance(task, dict):
        return
    ld = task.get("lastDiagnosis") if isinstance(task.get("lastDiagnosis"), dict) else {}
    problem = str(ld.get("problem") or "").strip()
    if problem and not looks_generic_needs_user_text(problem):
        return
    inferred = _diagnosis_from_task_evidence(task)
    if not str(inferred.get("problem") or "").strip():
        return
    merged = dict(ld)
    merged.update(inferred)
    task["lastDiagnosis"] = merged


def _format_task_evidence_for_options(task: Dict[str, Any]) -> str:
    """Compact evidence block for the options LLM prompt."""
    lines: List[str] = []
    outcome = task.get("lastStepOutcome") if isinstance(task.get("lastStepOutcome"), dict) else {}
    if outcome:
        for key in ("exitReason", "suggestedAction", "agentResultSnippet", "whyCardStayed"):
            val = str(outcome.get(key) or "").strip()
            if val:
                lines.append(f"{key}: {val[:300]}")
    step_diag = (
        task.get("lastStepDiagnostics") if isinstance(task.get("lastStepDiagnostics"), dict) else {}
    )
    if step_diag:
        perf = step_diag.get("performanceSummary")
        if isinstance(perf, dict):
            lines.append(f"performanceSummary: {perf}")
        writes = step_diag.get("writesSucceeded")
        if writes is not None:
            lines.append(f"writesSucceeded: {writes}")
    diagnostics = task.get("lastCommandDiagnostics") or []
    if isinstance(diagnostics, list):
        for i, d in enumerate(diagnostics[:5]):
            if isinstance(d, dict):
                path, line_s = _sanitize_diag_file(str(d.get("file") or ""), d.get("line"))
                msg = str(d.get("message") or "").strip()[:160]
                lines.append(f"lint[{i}]: {path}:{line_s} — {msg}")
    try:
        from backend.services.file_blocker import resolve_dev_edit_target_path

        target = resolve_dev_edit_target_path(task)
        if target:
            lines.append(f"editTarget: {target}")
    except Exception:
        pass
    return "\n".join(lines)[:4000]


def finalize_needs_user_options(task: Dict[str, Any], *, try_llm: bool = True) -> None:
    """Rebuild generic options from evidence; optionally refine with LLM."""
    if not isinstance(task, dict):
        return
    _ensure_evidence_diagnosis(task)
    if needs_user_options_look_generic(task.get("needsUserOptions"), task=task):
        kind = str(task.get("needsUserKind") or "stuck_loop")
        question = str(task.get("userQuestion") or "")
        task["needsUserOptions"] = _build_needs_user_options(task, kind, question=question)
    if try_llm:
        try:
            enrich_needs_user_options(task)
        except Exception:
            pass


def _resolve_needs_user_kind(task: Dict[str, Any], kind: str, raw_msg: str) -> str:
    raw = str(raw_msg or "")
    if _looks_secret_ask(raw) or _looks_secret_ask(str(task.get("userQuestion") or "")):
        return "secret"
    if kind == "phase_cycle_cap" or task.get("phaseCycleCapReached"):
        return "phase_cycle_cap"
    if stuck_is_tool_or_lint(task):
        exit_r = _exit_reason(task)
        if (exit_r == "text_rejection_loop" or _text_rejection_park_message(raw)) and not is_lint_wall_card(
            task
        ):
            return "stuck_loop"
        return "lint"
    exit_r = _exit_reason(task)
    if exit_r in ("explore_budget_exhausted", "read_only_no_edits") or task.get(
        "forcePatchNextDevStep"
    ):
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


_OPTION_IDS = ("a", "b", "c", "d")
_OTHER_OPTION: Dict[str, str] = {
    "id": "other",
    "label": "Other (type below)",
    "answer": "",
    "target": "dev",
}
_BANNED_OPTION_SNIPPETS = (
    "proceed with the simplest option that matches the spec",
    "send back to product owner to refine the spec",
    "answer in my own words",
)
_PHASE_CAP_ONLY_LABELS = frozenset(
    {
        "split into smaller cards",
        "reset developer visit latch",
    }
)
_GENERIC_FALLBACK_OPTION_MARKERS = (
    "continue implementing",
    "with the current spec",
    "proceed with the simplest option",
    "send back to product owner to refine the spec",
    "answer in my own words",
)
_VISIT_CAP_QUESTION_MARKERS = (
    "visit latch",
    "reset the developer visit",
    "reset the phase cycle",
    "split into smaller cards, or reset",
)
_needs_user_options_chat: Optional[Callable[[str], str]] = None


def _short_label(text: str, limit: int = 160) -> str:
    t = " ".join(str(text or "").split())
    if not t:
        return ""
    if len(t) <= limit:
        return t
    return t[: limit - 1].rstrip() + "…"


def _is_banned_option_label(label: str) -> bool:
    lab = str(label or "").strip().lower()
    if not lab:
        return True
    if lab in ("other", "other (type below)"):
        return True
    return any(s in lab for s in _BANNED_OPTION_SNIPPETS)


def _target_from_suggested_agent(suggested: str) -> str:
    s = str(suggested or "").strip().lower()
    if s in ("po", "product owner"):
        return "po"
    if s in ("refinement",):
        return "refinement"
    return "dev"


def _alternatives_from_question(question: str) -> List[str]:
    q = str(question or "").strip()
    if not q:
        return []
    lower = q.lower()
    if any(m in lower for m in _VISIT_CAP_QUESTION_MARKERS):
        return []
    vs = re.search(r"\b(.+?)\s+vs\.?\s+(.+?)\s*\??\s*$", q, re.I)
    if vs:
        left = vs.group(1).strip()
        right = vs.group(2).strip(" ?.")
        left = " ".join(left.split()[-6:])
        if 1 < len(left) < 80 and 1 < len(right) < 80:
            return [left, right]
    m = re.search(
        r"(?:be|use|choose|pick|prefer|want)\s+(.+?)\s+or\s+(.+?)\s*\??\s*$",
        q,
        re.I,
    )
    if m:
        a, b = m.group(1).strip(" .,"), m.group(2).strip(" ?,.")
        if 1 < len(a) < 80 and 1 < len(b) < 80:
            return [a, b]
    m = re.search(r":\s*(.+?)\s+or\s+(.+?)\s*\??\s*$", q, re.I)
    if m:
        a, b = m.group(1).strip(" .,"), m.group(2).strip(" ?,.")
        if 1 < len(a) < 80 and 1 < len(b) < 80:
            return [a, b]
    m = re.search(
        r"\b([A-Za-z][\w +/\-]{1,40})\s+or\s+([A-Za-z][\w +/\-]{1,40})\s*\??\s*$",
        q,
    )
    if m:
        a, b = m.group(1).strip(), m.group(2).strip(" ?.")
        if a.lower() not in ("split", "reset") and b.lower() not in ("split", "reset"):
            return [a, b]
    return []


def _finalize_options(raw: List[Dict[str, str]]) -> List[Dict[str, str]]:
    seen: set[str] = set()
    out: List[Dict[str, str]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or "").strip()
        if not label or _is_banned_option_label(label):
            continue
        key = normalize_question(label)[:120]
        if key in seen:
            continue
        seen.add(key)
        if len(out) >= 4:
            break
        target = str(item.get("target") or "dev").strip().lower()
        if target not in ("dev", "po", "refinement"):
            target = "dev"
        answer = str(item.get("answer") or label).strip() or label
        out.append(
            {
                "id": _OPTION_IDS[len(out)],
                "label": label[:160],
                "answer": answer[:500],
                "target": target,
            }
        )
    if not out:
        return []
    other = dict(_OTHER_OPTION)
    out.append(other)
    return out


def needs_user_options_look_generic(
    options: Any,
    *,
    task: Optional[Dict[str, Any]] = None,
) -> bool:
    """True when options match the old kind templates and should be rebuilt."""
    if not isinstance(options, list) or not options:
        return False
    labels: List[str] = []
    for opt in options:
        if not isinstance(opt, dict):
            continue
        if str(opt.get("id") or "") == "other":
            continue
        labels.append(re.sub(r"\s+", " ", str(opt.get("label") or "").strip().lower()))
    if not labels:
        return False
    if any(_is_banned_option_label(lab) for lab in labels):
        return True
    if set(labels) <= _PHASE_CAP_ONLY_LABELS:
        if task is None:
            return True
        diagnosis = task.get("lastDiagnosis") if isinstance(task.get("lastDiagnosis"), dict) else {}
        has_evidence = bool(
            (diagnosis or {}).get("recommendedAction")
            or (diagnosis or {}).get("problem")
            or _first_lint_line(task)
            or _last_failed_tool(task)
        )
        return has_evidence
    if any(any(m in lab for m in _GENERIC_FALLBACK_OPTION_MARKERS) for lab in labels):
        return True
    if len(labels) == 2 and all("split" in lab for lab in labels):
        return True
    return False


def _build_needs_user_options(
    task: Dict[str, Any],
    resolved_kind: str,
    *,
    question: str = "",
) -> List[Dict[str, str]]:
    """Evidence-based A/B/C/D + Other options for genuine user decisions."""
    _ensure_evidence_diagnosis(task)
    title = str(task.get("title") or task.get("id") or "this card")
    diagnosis = task.get("lastDiagnosis") if isinstance(task.get("lastDiagnosis"), dict) else {}
    problem = str((diagnosis or {}).get("problem") or "").strip()
    recommended = str((diagnosis or {}).get("recommendedAction") or "").strip()
    suggested_agent = str((diagnosis or {}).get("suggestedAgent") or "").strip()
    lint_line = _first_lint_line(task)
    failed_tool = _last_failed_tool(task)
    gaps = _spec_gap_lines(task)
    collected: List[Dict[str, str]] = []

    def add(label: str, answer: str, target: str = "dev") -> None:
        lab = str(label or "").strip()
        if not lab:
            return
        collected.append(
            {
                "label": _short_label(lab) or lab[:160],
                "answer": str(answer or lab).strip()[:500],
                "target": target,
            }
        )

    if recommended and not looks_generic_needs_user_text(recommended):
        add(recommended, recommended, _target_from_suggested_agent(suggested_agent))
    elif problem and not looks_generic_needs_user_text(problem):
        add(
            f"Resolve: {problem}",
            f"Resolve this blocker: {problem}",
            _target_from_suggested_agent(suggested_agent),
        )

    for alt in _alternatives_from_question(question):
        add(alt, f"Use {alt}.", "dev")

    if lint_line:
        add(
            f"Fix {lint_line}",
            f"Fix this lint/tool error first: {lint_line}",
            "dev",
        )

    diagnostics = task.get("lastCommandDiagnostics") or []
    if isinstance(diagnostics, list):
        seen_lint: set[str] = set()
        for d in diagnostics[1:4]:
            if not isinstance(d, dict):
                continue
            path, line_s = _sanitize_diag_file(str(d.get("file") or ""), d.get("line"))
            msg = str(d.get("message") or "").strip()
            if msg.startswith("error") and "•" in msg:
                inner = re.sub(r"^(?:error|warning|info)\s+[•\-]\s+", "", msg, flags=re.I)
                inner = re.split(r"\s+[•\-]\s+", inner, maxsplit=1)[0].strip()
                if inner:
                    msg = inner
            key = f"{path}:{line_s}:{msg[:80]}"
            if key in seen_lint or not msg:
                continue
            seen_lint.add(key)
            short_msg = _short_label(msg, 72) or msg[:72]
            add(
                f"Fix {path}:{line_s} — {short_msg}",
                f"In `{path}` at line {line_s}, fix: {msg}. Then re-run analyze.",
                "dev",
            )

    outcome = task.get("lastStepOutcome") if isinstance(task.get("lastStepOutcome"), dict) else {}
    outcome_action = str((outcome or {}).get("suggestedAction") or "").strip()
    if outcome_action and not looks_generic_needs_user_text(outcome_action):
        add(outcome_action, outcome_action, "dev")

    try:
        from backend.services.file_blocker import resolve_dev_edit_target_path

        edit_target = str(resolve_dev_edit_target_path(task) or "").strip()
        if edit_target and not any(edit_target in str(c.get("label") or "") for c in collected):
            title_lower = title.lower()
            if "pubspec" in edit_target or "pubspec" in title_lower:
                add(
                    f"Set SDK constraint in {edit_target}",
                    f"Update environment/sdk in `{edit_target}` to match the project, then run flutter pub get.",
                    "dev",
                )
            elif edit_target.endswith(".dart"):
                add(
                    f"Patch `{edit_target}` for this card",
                    f"Apply the fix in `{edit_target}` as described in the card title and lint output.",
                    "dev",
                )
    except Exception:
        pass

    if failed_tool:
        tool_name = failed_tool.split(":", 1)[0].strip() or "the last tool"
        add(
            f"Fix the {tool_name} failure and retry",
            f"Fix this tool failure, then continue: {failed_tool}",
            "dev",
        )

    if gaps:
        if "acceptance criteria are empty" in gaps:
            add(
                "Write 2–5 acceptance criteria",
                f'Write 2–5 done-when acceptance criteria for "{title}".',
                "po",
            )
        else:
            add(
                f"Fill spec: {gaps[0]}",
                f'Fill the spec gap for "{title}": {gaps[0]}.',
                "po",
            )

    if resolved_kind == "secret":
        add(
            "Use an environment variable (do not commit)",
            "Use an environment variable for the secret; do not commit credentials to the repo.",
            "dev",
        )
        add(
            "I will paste the secret in Other",
            "I will provide the secret value in a follow-up message.",
            "dev",
        )

    if resolved_kind == "phase_cycle_cap":
        add(
            "Split into smaller cards",
            f"Split '{title}' into smaller focused cards.",
            "po",
        )
        add(
            "Reset Developer visit latch",
            "Reset the phase cycle cap and continue implementation on this card.",
            "dev",
        )

    step_exit = _exit_reason(task)
    if resolved_kind in ("stuck", "stuck_loop") and not is_lint_wall_card(task):
        if step_exit == "text_rejection_loop" or _text_rejection_park_message(question):
            add(
                f'Split "{title}" into smaller cards',
                f"The model refused tools on this scope — split '{title}' into smaller cards.",
                "po",
            )
            add(
                "Manual edit in the IDE, then Send to Developer",
                "Edit the target file yourself, then click Send to Developer to continue.",
                "dev",
            )
            add(
                "Defer this feature",
                "Move this card out of the sprint and implement a smaller slice first.",
                "po",
            )

    if resolved_kind == "explore" and not any(
        "file" in str(c.get("label") or "").lower() for c in collected
    ):
        add(
            f'Name the first file/function to change for "{title}"',
            f'Change this first file/function to implement "{title}", then continue.',
            "dev",
        )

    finalized = _finalize_options(collected)
    real = [o for o in finalized if o.get("id") != "other"]
    if len(real) < 2:
        inferred = _diagnosis_from_task_evidence(task)
        rec = str(inferred.get("recommendedAction") or "").strip()
        prob = str(inferred.get("problem") or "").strip()
        if rec and not looks_generic_needs_user_text(rec):
            add(_short_label(rec, 120) or rec[:120], rec, "dev")
        if prob and not looks_generic_needs_user_text(prob):
            add(
                _short_label(f"Address: {prob}", 120) or prob[:120],
                f"Address this blocker: {prob}",
                "dev",
            )
        finalized = _finalize_options(collected)
        real = [o for o in finalized if o.get("id") != "other"]
    if len(real) < 2:
        add(
            f'Split "{title}" into smaller focused cards',
            f"Split '{title}' into smaller focused cards.",
            "po",
        )
        add(
            "Reset Developer visit latch and retry",
            "Reset the phase cycle cap on this card and run Developer again.",
            "dev",
        )
        finalized = _finalize_options(collected)
    return finalized


def build_needs_user_brief(
    task: Dict[str, Any],
    *,
    kind: str = "stuck_loop",
    raw_msg: str = "",
) -> Dict[str, Any]:
    """Structured Needs User copy: question, why, action, suggested resolve target.

    Does not call an LLM — uses diagnosis, lint, last step outcome, and spec gaps.
    """
    _ensure_evidence_diagnosis(task)
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
    elif resolved_kind == "phase_cycle_cap":
        if problem and not looks_generic_needs_user_text(problem):
            question = f"How should we resolve: {problem[:220]}?"
        elif lint_line:
            question = (
                "This card hit its Developer visit cap. "
                f"What should Developer do about: {lint_line}?"
            )
        elif recommended and not looks_generic_needs_user_text(recommended):
            question = f"How should we proceed: {recommended[:220]}?"
        else:
            question = (
                f'Split "{title}" into smaller cards, or reset the Developer visit latch '
                "so implementation can continue?"
            )
        why_parts.append(
            "This card hit its Developer visit cap. Auto Sprint will not run Product Owner "
            "or Developer on it until it is split or the latch is reset."
        )
        why_parts.append(
            "Failed reads, missing paths, patch mismatches, and lint are agent tool errors — "
            "not a product decision you can answer."
        )
        action = (
            "Split the card, or reset the phase cycle cap and click Send to Developer. "
            "Do not reply with file paths or 'the file does not exist'."
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
    elif resolved_kind in ("stuck", "stuck_loop") and not is_lint_wall_card(task):
        if exit_r == "text_rejection_loop" or _text_rejection_park_message(raw_msg):
            question = question or (
                f'Model refused tools on "{title}". Split the card or choose how to proceed?'
            )
            why_parts.append(
                "Developer returned text-only or safety refusals instead of apply_patch/write_file."
            )
            action = (
                "Choose split, manual edit, or defer — then Send to Developer or Product Owner."
            )
        elif not question:
            if problem:
                question = f"How should we resolve: {problem[:220]}?"
            else:
                question = (
                    f'What decision or missing fact does Developer need to continue "{title}"?'
                )
        if why_stayed:
            why_parts.append(why_stayed[:280])
        elif exit_r and exit_r != "text_rejection_loop":
            why_parts.append(f"Last step ended with {exit_r}.")
        if not action:
            action = (
                "Answer in one short message, then click Send to Developer to resume implementation. "
                "Use Send to Product Owner only to rewrite the spec."
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
        "options": _build_needs_user_options(task, resolved_kind, question=question),
    }


def apply_needs_user_brief(task: Dict[str, Any], brief: Dict[str, Any]) -> None:
    """Write structured Needs User fields onto the task."""
    task["userQuestion"] = str(brief.get("question") or "")[:500]
    task["needsUserReason"] = str(brief.get("why") or "")[:600]
    task["needsUserAction"] = str(brief.get("action") or "")[:600]
    task["needsUserKind"] = str(brief.get("kind") or "")[:40]
    target = str(brief.get("suggestedTarget") or "dev").strip().lower()
    if target not in ("dev", "po", "refinement"):
        target = "dev"
    task["needsUserSuggestedTarget"] = target
    options = brief.get("options")
    if isinstance(options, list) and options:
        task["needsUserOptions"] = options
    else:
        task.pop("needsUserOptions", None)
    finalize_needs_user_options(task, try_llm=True)


def _build_options_llm_prompt(task: Dict[str, Any], evidence: List[Dict[str, str]]) -> str:
    diagnosis = task.get("lastDiagnosis") if isinstance(task.get("lastDiagnosis"), dict) else {}
    ac = task.get("acceptanceCriteria") or []
    evidence_labels = [
        str(o.get("label") or "")
        for o in evidence
        if isinstance(o, dict) and str(o.get("id") or "") != "other"
    ]
    parts = [
        "You help a user unblock a kanban card. Propose 2-4 mutually exclusive concrete solutions they can pick.",
        "Each option is a decision or instruction, not a process meta-label.",
        'Do NOT use labels like "Proceed with the simplest option", "Send back to Product Owner to refine the spec", or "Answer in my own words".',
        "Do not invent secrets or credentials.",
        "Prefer specific files, commands, and product choices from the evidence.",
        "target must be one of: dev, po, refinement.",
        'Return ONLY JSON: {"options": [{"id":"a","label":"one-line solution","answer":"instruction to the agent","target":"dev"}]}',
        f"Task: {task.get('title') or task.get('id')}",
        f"Question: {task.get('userQuestion') or ''}",
        f"Why: {task.get('needsUserReason') or ''}",
        f"Acceptance criteria: {ac}",
    ]
    if diagnosis:
        parts.append(
            "Diagnosis: "
            + str(diagnosis.get("problem") or "")
            + " | "
            + str(diagnosis.get("recommendedAction") or "")
        )
    lint_line = _first_lint_line(task)
    if lint_line:
        parts.append(f"Lint: {lint_line}")
    failed = _last_failed_tool(task)
    if failed:
        parts.append(f"Last failed tool: {failed}")
    if evidence_labels:
        parts.append("Evidence options already considered: " + "; ".join(evidence_labels[:4]))
    evidence_block = _format_task_evidence_for_options(task)
    if evidence_block:
        parts.append("Step evidence:\n" + evidence_block)
    return "\n".join(parts)[:8000]


def _default_needs_user_options_chat(prompt: str) -> str:
    import os

    if os.environ.get("PYTEST_CURRENT_TEST"):
        return ""
    from backend.agents.registry import agent_dev
    from backend.services.llm_provider import build_provider, chat_config

    cfg = dict(chat_config())
    cfg["timeoutSec"] = 25
    provider = build_provider(cfg)
    model = str(getattr(agent_dev, "model", "") or "qwen2.5-coder:14b")
    result = provider.chat(
        model,
        [
            {
                "role": "system",
                "content": "Reply with valid JSON only. No markdown fences unless needed.",
            },
            {"role": "user", "content": prompt},
        ],
        options={"num_predict": 512, "temperature": 0.2},
    )
    message = getattr(result, "message", None)
    content = getattr(message, "content", None) if message is not None else None
    return str(content or "")


def enrich_needs_user_options(
    task: Dict[str, Any],
    *,
    chat_fn: Optional[Callable[[str], str]] = None,
) -> bool:
    """Best-effort LLM refine of needsUserOptions. Returns True when replaced."""
    if not isinstance(task, dict):
        return False
    existing = task.get("needsUserOptions")
    if not isinstance(existing, list):
        existing = []
    fn = _needs_user_options_chat if chat_fn is None else chat_fn
    if fn is None:
        fn = _default_needs_user_options_chat
    prompt = _build_options_llm_prompt(task, existing)
    try:
        raw = fn(prompt)
    except Exception:
        return False
    if not str(raw or "").strip():
        return False
    parsed: Optional[Dict[str, Any]] = None
    try:
        from backend.services.po_clarification import extract_json_object_from_text

        parsed = extract_json_object_from_text(str(raw))
    except Exception:
        parsed = None
    if not isinstance(parsed, dict):
        try:
            import json as _json

            loaded = _json.loads(str(raw))
            parsed = loaded if isinstance(loaded, dict) else None
        except Exception:
            return False
    if not isinstance(parsed, dict):
        return False
    incoming = parsed.get("options")
    if not isinstance(incoming, list):
        return False
    normalized: List[Dict[str, str]] = []
    for item in incoming:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or "").strip()
        if not label:
            continue
        normalized.append(
            {
                "label": label,
                "answer": str(item.get("answer") or label).strip(),
                "target": str(item.get("target") or "dev"),
            }
        )
    finalized = _finalize_options(normalized)
    if len([o for o in finalized if o.get("id") != "other"]) < 2:
        return False
    task["needsUserOptions"] = finalized
    return True


def refresh_generic_needs_user_options(
    task: Dict[str, Any],
    *,
    chat_fn: Optional[Callable[[str], str]] = None,
    with_llm: bool = False,
) -> bool:
    """Rebuild template A/B/C options from current evidence. Optional LLM refine."""
    if not isinstance(task, dict):
        return False
    if not needs_user_options_look_generic(task.get("needsUserOptions"), task=task):
        return False
    kind = str(task.get("needsUserKind") or "stuck_loop")
    raw_msg = str(task.get("userQuestion") or task.get("needsUserReason") or "")
    brief = build_needs_user_brief(task, kind=kind, raw_msg=raw_msg)
    apply_needs_user_brief(task, brief)
    return True


def needs_user_fields_empty(task: Dict[str, Any]) -> bool:
    question = str((task or {}).get("userQuestion") or "").strip()
    return not question or looks_generic_needs_user_text(question)


def _raw_msg_from_task_evidence(task: Dict[str, Any]) -> str:
    outcome = task.get("lastStepOutcome") if isinstance(task.get("lastStepOutcome"), dict) else {}
    progress = (
        task.get("lastStepProgress") if isinstance(task.get("lastStepProgress"), dict) else {}
    )
    return (
        str((outcome or {}).get("whyCardStayed") or "").strip()
        or str((progress or {}).get("whyCardStayed") or "").strip()
        or str((outcome or {}).get("message") or "").strip()
        or str(task.get("needsUserReason") or "").strip()
    )


def ensure_needs_user_brief(
    task: Dict[str, Any],
    *,
    kind: str = "",
    raw_msg: str = "",
) -> bool:
    """Fill Question / Why / How when a Needs User card has no real question.

    Returns True when fields were written.
    """
    if not isinstance(task, dict):
        return False
    if needs_user_fields_empty(task):
        brief = build_needs_user_brief(
            task,
            kind=kind or "stuck_loop",
            raw_msg=raw_msg or _raw_msg_from_task_evidence(task),
        )
        apply_needs_user_brief(task, brief)
        return True
    if refresh_generic_needs_user_options(task):
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
