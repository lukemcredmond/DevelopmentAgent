"""Composable sprint prompt sections and FocusContext for micro-step prompts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from backend import state
from backend.agents.task_context import (
    build_dependency_outcome,
    coerce_task_text,
    find_task_by_id,
    is_task_done,
    normalize_task,
)
from backend.services.workflow_settings import get_workflow_settings
from backend.services.prompt_profile import is_local_slm_profile, local_slm_sections_for_role

SECTION_IDS = (
    "brief_slice",
    "card_core",
    "ac_focus",
    "scope_focus",
    "user_story",
    "task_spec_summary",
    "task_spec_full",
    "dod",
    "working_context",
    "dependencies",
    "related_cards",
    "qa_failure",
    "last_outcome",
    "decisions_recent",
    "transcript_recent",
    "project_evidence",
    "workspace_file_list",
    "codebase_pack",
    "lane_instructions",
)

ALL_SECTIONS_FULL: List[str] = [
    "brief_slice",
    "dod",
    "working_context",
    "card_core",
    "ac_focus",
    "scope_focus",
    "user_story",
    "project_evidence",
    "qa_failure",
    "last_outcome",
    "dependencies",
    "related_cards",
    "decisions_recent",
    "task_spec_full",
    "transcript_recent",
    "codebase_pack",
]

ALL_SECTIONS_SLIM: List[str] = [
    "brief_slice",
    "dod",
    "working_context",
    "card_core",
    "ac_focus",
    "project_evidence",
    "qa_failure",
    "last_outcome",
    "dependencies",
    "related_cards",
    "decisions_recent",
    "task_spec_summary",
    "transcript_recent",
]


@dataclass
class FocusContext:
    agent_role: str = ""
    focus_mode: str = "whole"
    ac_index: Optional[int] = None
    subtask_id: Optional[str] = None
    include_full_spec: bool = True


@dataclass
class _PromptLimits:
    slim: bool
    decision_limit: int
    transcript_limit: int
    related_limit: int
    dependency_limit: int
    num_ctx: int


def _limits_for_role(role: str) -> _PromptLimits:
    from backend.services.prompt_budget import resolve_ollama_num_ctx

    if is_local_slm_profile():
        num_ctx = resolve_ollama_num_ctx(
            "qa" if role == "QA Tester" else ("cr" if role == "Code Reviewer" else "dev")
        )
        return _PromptLimits(
            slim=True,
            decision_limit=0,
            transcript_limit=0,
            related_limit=0,
            dependency_limit=2,
            num_ctx=num_ctx,
        )
    slim = role in ("Code Reviewer", "QA Tester")
    num_ctx = resolve_ollama_num_ctx(
        "qa" if role == "QA Tester" else ("cr" if role == "Code Reviewer" else "dev")
    )
    return _PromptLimits(
        slim=slim,
        decision_limit=3 if slim else 8,
        transcript_limit=2 if slim else 6,
        related_limit=2 if slim else 5,
        dependency_limit=3 if slim else 10,
        num_ctx=num_ctx,
    )


def full_sections_for_role(role: str, focus: FocusContext) -> List[str]:
    if focus.include_full_spec or focus.focus_mode == "whole":
        return ALL_SECTIONS_SLIM if _limits_for_role(role).slim else ALL_SECTIONS_FULL
    return [
        "brief_slice",
        "card_core",
        "ac_focus",
        "scope_focus",
        "user_story",
        "task_spec_summary",
        "dod",
        "working_context",
        "dependencies",
        "related_cards",
        "qa_failure",
        "last_outcome",
        "decisions_recent",
        "transcript_recent",
        "project_evidence",
        "workspace_file_list",
        "codebase_pack",
    ]


def _truncate_section(text: str, max_chars: int = 12000) -> str:
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 35] + "\n...[section truncated]\n"


def compose_prompt(
    task: Dict[str, Any],
    brief: str,
    section_ids: List[str],
    focus: FocusContext,
    *,
    agent_role: Optional[str] = None,
    codebase_pack: str = "",
) -> str:
    role = (agent_role or focus.agent_role or state.ACTIVE_SPRINT_AGENT or "").strip()
    limits = _limits_for_role(role)
    from backend.services.prompt_budget import truncate_brief

    brief = truncate_brief(brief, limits.num_ctx)
    normalize_task(task)

    if is_local_slm_profile():
        section_ids = section_ids or local_slm_sections_for_role(role, task)
        parts: List[str] = []
        for sid in section_ids:
            block = build_section(
                sid,
                task,
                brief,
                focus=focus,
                limits=limits,
                codebase_pack=codebase_pack,
            )
            if block and block.strip():
                parts.append(block.strip())
        return "\n\n".join(parts) + ("\n" if parts else "")

    # Full-stack parity with build_task_prompt_legacy when requested.
    if focus.include_full_spec and focus.focus_mode == "whole":
        role = (agent_role or focus.agent_role or state.ACTIVE_SPRINT_AGENT or "").strip()
        if role in ("Code Reviewer", "QA Tester", "Product Owner", ""):
            from backend.agents.task_context import build_task_prompt_legacy

            return build_task_prompt_legacy(task, brief, agent_role=role or None)

    parts: List[str] = []
    for sid in section_ids:
        block = build_section(
            sid,
            task,
            brief,
            focus=focus,
            limits=limits,
            codebase_pack=codebase_pack,
        )
        if block and block.strip():
            parts.append(block.strip())
    return "\n\n".join(parts) + ("\n" if parts else "")


def build_section(
    section_id: str,
    task: Dict[str, Any],
    brief: str,
    *,
    focus: FocusContext,
    limits: _PromptLimits,
    codebase_pack: str = "",
) -> str:
    if section_id == "brief_slice":
        return f"Project brief:\n{brief}\n"
    if section_id == "dod":
        from backend.agents.task_context import build_dod_block

        return build_dod_block().strip()
    if section_id == "working_context":
        from backend.services.task_working_context import format_working_context_for_prompt

        max_lines = 3 if is_local_slm_profile() else 12
        wc = format_working_context_for_prompt(task, max_lines=max_lines)
        return (wc + "\n") if wc else ""
    if section_id == "card_core":
        return _section_card_core(task, focus)
    if section_id == "ac_focus":
        return _section_ac_focus(task, focus)
    if section_id == "scope_focus":
        return _section_scope_focus(task, focus)
    if section_id == "user_story":
        story = coerce_task_text(task.get("userStory") or "").strip()
        return f"User story:\n{story}\n" if story else ""
    if section_id == "task_spec_summary":
        return _section_task_spec_summary(task, focus)
    if section_id == "task_spec_full":
        return _section_task_spec_full(task)
    if section_id == "project_evidence":
        return _section_project_evidence()
    if section_id == "qa_failure":
        return _section_qa_failure(task)
    if section_id == "last_outcome":
        from backend.agents.task_context import _format_last_step_outcome_block

        return (_format_last_step_outcome_block(task) or "").strip()
    if section_id == "dependencies":
        return _section_dependencies(task, limits, focus)
    if section_id == "related_cards":
        return _section_related_cards(task, limits, focus)
    if section_id == "decisions_recent":
        return _section_decisions(task, limits)
    if section_id == "transcript_recent":
        return _section_transcript(task, limits)
    if section_id == "workspace_file_list":
        return _section_workspace_files(limits)
    if section_id == "codebase_pack":
        if codebase_pack and codebase_pack.strip():
            from backend.services.prompt_budget import codebase_pack_max_chars_for_prompt
            from backend.services.prompt_profile import is_local_slm_profile as _local_slm

            pack_cap = codebase_pack_max_chars_for_prompt(local_slm=_local_slm())
            return (
                "=== CODEBASE PACK (external CLI summary — prefer for navigation) ===\n"
                + _truncate_section(codebase_pack.strip(), pack_cap)
            )
        return ""
    if section_id == "lane_instructions":
        return ""
    return ""


def _section_card_core(task: Dict[str, Any], focus: FocusContext) -> str:
    blocked = task.get("blockedBy") or []
    blocked_str = ", ".join(blocked) if blocked else "(none)"
    task_files = task.get("files") or []
    task_file_lines = []
    for f in task_files:
        if isinstance(f, str):
            task_file_lines.append(f)
        else:
            task_file_lines.append(f"{f.get('path', '?')} ({f.get('action', 'touched')})")
    task_files_str = ", ".join(task_file_lines) if task_file_lines else "(none yet)"
    desc = coerce_task_text(task.get("description") or "")
    if focus.focus_mode == "ac" and not focus.include_full_spec:
        desc = desc[:600] + ("…" if len(desc) > 600 else "")
    lines = [
        f"Task ID: {task['id']}",
        f"Title: {task['title']}",
        f"Description: {desc}",
        f"Blocked by (must be Done first): {blocked_str}",
        f"Current status: {task.get('status', 'unknown')}",
        f"Files associated with this card: {task_files_str}",
    ]
    extra = ""
    subtasks = task.get("subtaskIds") or []
    if subtasks and focus.include_full_spec:
        sub_lines = []
        for sid in subtasks:
            sub = find_task_by_id(str(sid))
            if sub:
                done = is_task_done(str(sid))
                sub_lines.append(
                    f"- {sid}: {sub.get('title', '?')} (order {sub.get('executionOrder', '?')}, "
                    f"{'Done' if done else sub.get('status', '?')})"
                )
            else:
                sub_lines.append(f"- {sid}: (missing)")
        extra += (
            "\n=== SUBTASKS (must all reach Done before this card completes) ===\n"
            + "\n".join(sub_lines)
            + "\n"
        )
    elif focus.focus_mode == "subtask" and focus.subtask_id:
        sub = find_task_by_id(focus.subtask_id)
        if sub:
            extra += f"\n=== FOCUS SUBTASK ===\n{focus.subtask_id}: {sub.get('title', '?')}\n"
    if task.get("parentTaskId"):
        extra += f"\nParent todo: {task['parentTaskId']}\n"
    if task.get("featureId") and focus.include_full_spec:
        from backend.services.feature_service import build_feature_context_block

        extra += build_feature_context_block(str(task["featureId"]))
    return "\n".join(lines) + extra


def _section_ac_focus(task: Dict[str, Any], focus: FocusContext) -> str:
    ac_lines = [str(c).strip() for c in (task.get("acceptanceCriteria") or []) if str(c).strip()]
    if not ac_lines:
        return "Acceptance criteria:\n(none defined)\n"
    if focus.focus_mode == "ac" and not focus.include_full_spec:
        idx = focus.ac_index if focus.ac_index is not None else int(task.get("focusAcIndex") or 0)
        idx = max(0, min(idx, len(ac_lines) - 1))
        return (
            "=== ACCEPTANCE CRITERION (this step only) ===\n"
            f"- [{idx + 1}/{len(ac_lines)}] {ac_lines[idx]}\n"
            "Verify and implement only this criterion in this step. "
            "Call update_board with decision type focus_done when satisfied.\n"
        )
    if is_local_slm_profile():
        ac_lines = ac_lines[:3]
    ac_str = "\n".join(f"- {c}" for c in ac_lines)
    return f"Acceptance criteria:\n{ac_str}\n"


def _section_scope_focus(task: Dict[str, Any], focus: FocusContext) -> str:
    if focus.focus_mode == "ac" and not focus.include_full_spec:
        scope = coerce_task_text(task.get("scope") or "").strip()
        if scope:
            return f"In scope (summary):\n{scope[:800]}\n"
        return ""
    scope = coerce_task_text(task.get("scope") or "").strip()
    oos = coerce_task_text(task.get("outOfScope") or "").strip()
    parts = []
    if scope:
        parts.append(f"In scope:\n{scope}\n")
    if oos:
        parts.append(f"Out of scope:\n{oos}\n")
    return "".join(parts)


def _section_task_spec_summary(task: Dict[str, Any], focus: FocusContext) -> str:
    cached = coerce_task_text(task.get("focusSpecSummary") or "").strip()
    if cached:
        return (
            "=== TASK SPEC (summary for this focus slice) ===\n"
            + _truncate_section(cached, 4000)
        )
    try:
        from backend.services.task_spec_markdown import read_task_spec_markdown_for_prompt

        spec_doc = read_task_spec_markdown_for_prompt(task)
        if not spec_doc:
            return ""
        lines = spec_doc.splitlines()
        summary = "\n".join(lines[:45])
        if len(lines) > 45:
            summary += "\n...[spec truncated — full spec on QA/CR or whole-card mode]\n"
        task["focusSpecSummary"] = summary
        return f"=== TASK SPEC (summary for this focus slice) ===\n{summary}\n"
    except Exception:
        return ""


def _section_task_spec_full(task: Dict[str, Any]) -> str:
    try:
        from backend.services.task_spec_markdown import read_task_spec_markdown_for_prompt

        spec_doc = read_task_spec_markdown_for_prompt(task)
        if spec_doc:
            return (
                "=== TASK SPEC (authoritative — implement and verify against this) ===\n"
                f"{spec_doc}\n"
            )
    except Exception:
        pass
    return ""


def _section_project_evidence() -> str:
    try:
        from backend.services.project_evidence import format_project_evidence_for_prompt

        shared = format_project_evidence_for_prompt()
        return shared if shared else ""
    except Exception:
        return ""


def _section_qa_failure(task: Dict[str, Any]) -> str:
    qa_fail = task.get("qaFailure")
    if not qa_fail:
        return ""
    return (
        "=== LAST QA FAILURE ===\n"
        f"Reason: {qa_fail.get('reason', '')}\n"
        f"Output: {qa_fail.get('output', '')[:500]}\n"
        f"When: {qa_fail.get('timestamp', '')}\n"
    )


def _section_dependencies(task: Dict[str, Any], limits: _PromptLimits, focus: FocusContext) -> str:
    dep_limit = 2 if (focus.focus_mode == "ac" and not focus.include_full_spec) else limits.dependency_limit
    dependency_outcomes = task.get("dependencyOutcomes") or []
    if not dependency_outcomes:
        ld = task.get("lastDiagnosis")
        if isinstance(ld, dict) and (ld.get("problem") or ld.get("rootCause")):
            block = (
                "=== LAST DIAGNOSIS ===\n"
                f"Problem: {str(ld.get('problem') or '')[:400]}\n"
            )
            if ld.get("rootCause"):
                block += f"Root cause: {str(ld.get('rootCause'))[:300]}\n"
            if ld.get("suggestedFix"):
                block += f"Suggested fix: {str(ld.get('suggestedFix'))[:300]}\n"
            block += "Act on this diagnosis before exploring unrelated files.\n"
            return block
        return ""
    out = "=== COMPLETED DEPENDENCY OUTCOMES ===\n"
    for outcome in dependency_outcomes[-dep_limit:]:
        if not isinstance(outcome, dict):
            continue
        out += (
            f"\n[{outcome.get('taskId')}] {outcome.get('title', '?')} "
            f"(done {outcome.get('completedAt', '?')})\n"
            f"Summary: {outcome.get('summary', '')}\n"
        )
        if outcome.get("refinementNotes"):
            out += f"Refinement notes: {outcome['refinementNotes'][:400]}\n"
        if outcome.get("spikeReport"):
            out += f"Spike report: {outcome['spikeReport'][:400]}\n"
        files = outcome.get("files") or []
        if files:
            out += f"Key files: {', '.join(str(f) for f in files[:6])}\n"
        for decision in (outcome.get("decisions") or [])[:2]:
            if isinstance(decision, dict):
                out += f"  - {decision.get('agent', '?')}: {decision.get('summary', '')}\n"
    out += "Use these completed dependency results — do not redo finished blocker work.\n"
    return out


def _section_related_cards(task: Dict[str, Any], limits: _PromptLimits, focus: FocusContext) -> str:
    rel_limit = min(3, limits.related_limit)
    if focus.focus_mode == "ac" and not focus.include_full_spec:
        rel_limit = 2
    related_ids = [str(r) for r in (task.get("relatedTaskIds") or []) if r][:rel_limit]
    blocked = [str(b) for b in (task.get("blockedBy") or []) if b][:rel_limit]
    ids = list(dict.fromkeys(blocked + related_ids))
    if task.get("parentTaskId"):
        ids = list(dict.fromkeys([str(task["parentTaskId"])] + ids))
    if task.get("featureId") and len(ids) < rel_limit:
        pass
    if not ids:
        return ""
    related_blocks: List[str] = []
    for rid in ids[:rel_limit]:
        related = find_task_by_id(rid)
        if not related:
            continue
        normalize_task(related)
        done = is_task_done(rid)
        has_useful = bool(related.get("decisions") or related.get("files") or done)
        if not has_useful and not done:
            related_blocks.append(
                f"[{rid}] {related.get('title', '?')} — status {related.get('status', '?')} "
                "(in flight; reuse, do not recreate)"
            )
            continue
        outcome = build_dependency_outcome(related)
        status_label = "Done" if done else str(related.get("status") or "in flight")
        block = (
            f"[{rid}] {outcome.get('title', '?')} ({status_label})\n"
            f"Summary: {outcome.get('summary', '')}\n"
        )
        files = outcome.get("files") or []
        if files:
            block += f"Key files: {', '.join(str(f) for f in files[:6])}\n"
        for decision in (outcome.get("decisions") or [])[:2]:
            if isinstance(decision, dict):
                block += f"  - {decision.get('agent', '?')}: {decision.get('summary', '')}\n"
        related_blocks.append(block)
    if not related_blocks:
        return ""
    return (
        "=== RELATED WORK (reuse — do not redo) ===\n"
        "Related work already done / in flight — reuse outputs, do not recreate the same request.\n"
        + "\n".join(related_blocks)
        + "\n"
    )


def _section_decisions(task: Dict[str, Any], limits: _PromptLimits) -> str:
    from backend.agents.task_context import _format_older_decisions_block

    decisions = task.get("decisions") or []
    out = ""
    if not limits.slim:
        older = _format_older_decisions_block(decisions)
        if older:
            out += older
    if not decisions:
        return out.strip()
    out += "\n=== PRIOR AGENT DECISIONS ON THIS CARD ===\n"
    if not limits.slim:
        out += (
            "Tool reuse guidance: For identical run_command results (lint/test/--version/--help), "
            "prefer the summaries below — do not re-run the same command with the same args unless "
            "you edited related code or are fixing a prior failure. "
            "For read_file / list_dir / grep / glob_file_search, treat prior notes as hints only; "
            "re-read after any write_file or apply_patch (or if unsure the workspace changed). "
            "Do not invent paths from old directory listings alone.\n"
        )
    for d in decisions[-limits.decision_limit :]:
        out += (
            f"[{d.get('timestamp', '?')}] {d.get('agent', 'Agent')} "
            f"({d.get('type', 'note')}): {d.get('summary', '')}\n"
        )
        if d.get("detail") and not limits.slim:
            out += f"  Detail: {d['detail'][:300]}\n"
    resolutions = task.get("userResolutions") or []
    from backend.agents.task_context import _format_older_resolutions_block

    older_res = _format_older_resolutions_block(resolutions)
    if older_res:
        out = older_res + out
    if resolutions:
        out += "\n=== PRIOR USER ANSWERS (do not re-ask) ===\n"
        for res in resolutions[-5:]:
            if not isinstance(res, dict):
                continue
            out += (
                f"Q: {res.get('question', '')[:300]}\n"
                f"A: {res.get('answer', '')[:400]}\n"
                f"(→ {res.get('targetLane', '?')} at {res.get('timestamp', '?')})\n\n"
            )
        out += "These questions were already answered — do not escalate again for the same topic.\n"
    if task.get("userQuestion"):
        out += f"\n=== USER QUESTION PENDING ===\n{task['userQuestion']}\n"
    notes = coerce_task_text(task.get("refinementNotes") or "")
    if notes:
        out += f"\n=== REFINEMENT NOTES ===\n{notes[:2000]}\n"
    spike_report = coerce_task_text(task.get("spikeReport") or "")
    if spike_report:
        out += f"\n=== SPIKE REPORT ===\n{spike_report[:2000]}\n"
    try:
        from backend.services.task_qa_markdown import read_task_qa_markdown_for_prompt

        qa_doc = read_task_qa_markdown_for_prompt(task)
        if qa_doc:
            out += (
                "\n=== TASK Q&A DOC (summarized working notes — prefer over re-probing) ===\n"
                f"{qa_doc}\n"
            )
    except Exception:
        pass
    return out.strip()


def _section_transcript(task: Dict[str, Any], limits: _PromptLimits) -> str:
    if not task.get("transcript"):
        return ""
    out = "=== TASK TRANSCRIPT ===\n"
    for entry in task["transcript"][-limits.transcript_limit :]:
        out += f"[{entry.get('timestamp', '?')}] {entry.get('agent', entry.get('role', '?'))}: {entry.get('content', '')[:200]}\n"
    return out


def _section_workspace_files(limits: _PromptLimits) -> str:
    from backend.services.prompt_budget import workspace_file_list_cap

    paths = sorted(state.VIRTUAL_FILESYSTEM.keys())
    cap = workspace_file_list_cap(limits.num_ctx)
    if len(paths) > cap:
        file_list = ", ".join(paths[:cap]) + f", … (+{len(paths) - cap} more)"
    else:
        file_list = ", ".join(paths) or "(empty workspace)"
    return f"Workspace files: {file_list}\n"
