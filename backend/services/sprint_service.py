import json
import os
import random
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from backend import state
from backend.agents.registry import agent_cr, agent_dev, agent_po, agent_qa
from backend.agents.task_context import (
    all_task_ids,
    apply_po_clarification,
    assign_unique_task_id,
    build_dod_block,
    build_task_prompt,
    clear_active_sprint_context,
    clear_qa_failure,
    coerce_task_text,
    detect_blocked_by_issues,
    find_task_by_id,
    format_dependency_block_status,
    get_task_lane,
    increment_po_round_trips,
    init_new_task,
    create_spike_task,
    is_backlog_claimable,
    is_refinement_claimable,
    is_task_done,
    next_claimable_backlog_task,
    next_po_planning_backlog_task,
    next_refinement_task,
    next_spike_task,
    normalize_acceptance_criteria,
    normalize_task,
    publish_activity,
    record_task_decision,
    record_task_file,
    record_task_git_commit,
    set_active_sprint_context,
    set_qa_failure,
    sort_backlog,
    task_dependencies_met,
)
from backend.services.board_lanes import normalize_board_lanes
from backend.services.board_service import append_backlog_tasks, move_board_stage, publish_board_delta, publish_board_update
from backend.services.brief_service import (
    append_feature_to_brief,
    append_brief_text,
    existing_backlog_titles,
    looks_like_usable_plan_outline,
    record_brief_changelog,
    resolve_brief_for_sprint,
    set_project_plan_outline,
)
from backend.services.prompt_profile import is_local_slm_profile, po_planning_guidance_block
from backend.services.events import publish_event
from backend.services.feature_service import (
    apply_plan_epics_from_po_output,
    build_feature_context_for_po,
    create_feature,
    find_feature_by_id,
    intake_feature_offline,
    list_features,
    parse_po_feature_intake,
    update_feature,
)
from backend.services.feature_similarity import iter_board_tasks, link_related_features, score_task_similarity
from backend.services.git_service import git_commit, git_init
from backend.services.logs import add_system_log
from backend.services.needs_user_guard import (
    apply_needs_user_brief,
    build_needs_user_brief,
    build_stuck_escalation_message,
    clear_needs_user_fields,
    dev_clarification_from_result,
    dev_explicit_needs_user,
    enrich_needs_user_options,
    prefer_po_instruction_suffix,
    is_lint_wall_card,
    reroute_tool_blocker_to_dev,
    should_escalate_to_needs_user,
    should_park_in_needs_user,
    stuck_is_tool_or_lint,
)
from backend.services.project_service import save_current_project_state
from backend.services.workflow_settings import (
    get_active_lanes,
    get_last_sprint_summary,
    get_workflow_settings,
    save_sprint_summary,
)
from backend.services.tool_execution_service import log_synthetic_tool_event
from backend.workspace.files import (
    build_sprint_file_context,
    derive_project_test_commands,
    derive_project_lint_command,
    run_agent_command,
    write_workspace_file,
)

CONTEXT_INJECT_NOTE = (
    "Pre-loaded file context is for orientation only (often excerpts) and may be stale. "
    "Always call read_file in this step immediately before apply_patch on a path — "
    "apply_patch rejects preloaded text."
)

PLANNING_TASK_ID = "PLANNING"
PLANNING_OUTLINE_TASK_ID = "PLANNING_OUTLINE"
PLANNING_BACKLOG_TASK_ID = "PLANNING_BACKLOG"
_PLANNING_TASK_IDS = frozenset(
    {PLANNING_TASK_ID, PLANNING_OUTLINE_TASK_ID, PLANNING_BACKLOG_TASK_ID}
)
LLM_CALL_FAILED_PREFIX = "LLM_CALL_FAILED:"


def is_planning_task_id(task_id: Optional[str]) -> bool:
    return str(task_id or "") in _PLANNING_TASK_IDS


def _is_llm_call_failed(output: str) -> bool:
    return str(output or "").startswith(LLM_CALL_FAILED_PREFIX)


def _log_llm_call_failed(output: str, context: str) -> None:
    add_system_log("Product Owner", "error", f"{context} {output}")


def _po_last_chat_error() -> str | None:
    detail = str(getattr(agent_po, "_last_chat_error", None) or "").strip()
    return detail[:400] if detail else None

_HANDLER_AGENT: Dict[str, str] = {
    "po": "Product Owner",
    "dev": "Developer",
    "dev_recovery": "System",
    "cr": "Code Reviewer",
    "qa": "QA Tester",
    "refinement_dev": "Developer",
    "refinement_po": "Product Owner",
    "needs_user": "System",
    "blocked": "System",
    "idle": "System",
}


def publish_sprint_progress(
    *,
    phase: str,
    step: int = 0,
    max_steps: int = 20,
    agent: str = "System",
    task_id: str = "",
    task_title: str = "",
    lane: str = "",
    status: Optional[str] = None,
    intent: Optional[str] = None,
    card_progress: Optional[Dict[str, Any]] = None,
    focus_ac_index: Optional[int] = None,
    focus_subtask_id: Optional[str] = None,
    prompt_section: Optional[str] = None,
    ollama_wait_sec: Optional[int] = None,
    ollama_wait_max_sec: Optional[int] = None,
    phase_cycle_cap_reached: Optional[bool] = None,
) -> None:
    """Broadcast live Plan & Run / sprint step progress to SSE clients."""
    payload: Dict[str, Any] = {
        "phase": phase,
        "step": step,
        "maxSteps": max_steps,
        "agent": agent,
        "taskId": task_id,
        "taskTitle": task_title,
        "lane": lane,
    }
    if status:
        payload["status"] = status
    if intent:
        payload["intent"] = intent
    if card_progress:
        payload["cardProgress"] = card_progress
    if focus_ac_index is not None:
        payload["focusAcIndex"] = focus_ac_index
    if focus_subtask_id:
        payload["focusSubtaskId"] = focus_subtask_id
    if prompt_section:
        payload["promptSection"] = prompt_section
    if ollama_wait_sec is not None:
        payload["ollamaWaitSec"] = max(0, int(ollama_wait_sec))
    if ollama_wait_max_sec is not None:
        payload["ollamaWaitMaxSec"] = max(1, int(ollama_wait_max_sec))
    if phase_cycle_cap_reached is not None:
        payload["phaseCycleCapReached"] = bool(phase_cycle_cap_reached)
    try:
        from backend.services.prompt_budget import describe_num_ctx_clamp

        fit = describe_num_ctx_clamp(agent)
        if fit:
            payload["numCtxFit"] = fit
            if fit.get("label"):
                payload["numCtxLabel"] = fit["label"]
    except Exception:
        pass
    publish_event("sprint_progress", payload)


def _emit_sprint_step_progress(
    handler: str,
    active_task: Optional[Dict[str, Any]],
) -> None:
    step = state.SPRINT_PROGRESS_STEP or 1
    max_steps = state.SPRINT_PROGRESS_MAX or int(get_workflow_settings().get("maxSprintSteps", 20))
    task_id = str(active_task.get("id", "")) if active_task else ""
    task_title = str(active_task.get("title", handler)) if active_task else handler
    lane = get_task_lane(task_id) if task_id else ""
    publish_sprint_progress(
        phase="sprint_step",
        step=step,
        max_steps=max_steps,
        agent=_HANDLER_AGENT.get(handler, "System"),
        task_id=task_id,
        task_title=task_title,
        lane=lane or "",
    )


def _prepare_single_step_progress(*, force: bool = False) -> bool:
    """Configure progress for one manual step (Execute Step / Run In Progress)."""
    if not force and state.SPRINT_PROGRESS_STEP > 0 and state.SPRINT_PROGRESS_MAX > 1:
        return False
    state.LAST_STEP_OUTCOME = None
    state.LAST_AGENT_STEP_RESULT = None
    state.DEV_STEP_READ_ONLY_NO_EDITS = False
    state.DEV_STEP_COMMAND_REPEAT_NO_PROGRESS = False
    state.DEV_STEP_INTERRUPTED = False
    state.LAST_STEP_DIAGNOSTICS = None
    from backend.services.step_diagnostics import clear_active_step_trace

    clear_active_step_trace()
    state.SPRINT_PROGRESS_MAX = 1
    state.SPRINT_PROGRESS_STEP = 1
    return True


def _step_transcript_tools_since(
    task: Dict[str, Any],
    since: Optional[str],
) -> tuple[bool, bool]:
    """Return (has_read, has_write) for tool entries since step start timestamp."""
    has_read = False
    has_write = False
    for entry in task.get("transcript") or []:
        if not isinstance(entry, dict):
            continue
        if since and str(entry.get("timestamp") or "") < since:
            continue
        name = entry.get("toolName")
        if not name:
            continue
        if entry.get("toolSuccess") is False:
            continue
        if name in (
            "read_file",
            "list_dir",
            "glob_file_search",
            "grep",
            "search_code",
        ):
            has_read = True
        elif name in ("write_file", "apply_patch"):
            has_write = True
    return has_read, has_write


def _dev_step_repeated_command_no_progress(
    task: Dict[str, Any],
    lane_before: str,
    step_started: str,
) -> bool:
    lane_after = get_task_lane(str(task.get("id", ""))) or lane_before
    if lane_before != lane_after or lane_after != "In Progress":
        return False
    if _task_has_write_files(task):
        return False
    _, has_write = _step_transcript_tools_since(task, step_started)
    if has_write:
        return False
    from backend.services.duplicate_tool_policy import normalize_run_command_for_duplicate

    counts: Dict[str, int] = {}
    for entry in task.get("transcript") or []:
        if not isinstance(entry, dict):
            continue
        if step_started and str(entry.get("timestamp") or "") < step_started:
            continue
        if entry.get("toolName") != "run_command":
            continue
        if entry.get("toolSuccess") is False:
            continue
        args = entry.get("toolArgs") if isinstance(entry.get("toolArgs"), dict) else {}
        cmd = normalize_run_command_for_duplicate(str(args.get("command") or ""))
        if not cmd:
            continue
        counts[cmd] = counts.get(cmd, 0) + 1
    return any(n >= 2 for n in counts.values())


def _dev_step_read_only_no_edits(
    task: Dict[str, Any],
    lane_before: str,
    step_started: str,
) -> bool:
    lane_after = get_task_lane(str(task.get("id", ""))) or lane_before
    if lane_before != lane_after or lane_after != "In Progress":
        return False
    # Writes (not reads) prove real edits — reads alone must still count as read-only.
    if _task_has_write_files(task):
        return False
    has_read, has_write = _step_transcript_tools_since(task, step_started)
    return has_read and not has_write


def apply_read_only_no_edits_outcome(task: Dict[str, Any]) -> None:
    """Keep the card In Progress and force a Patch turn after a read-only Dev step."""
    task["forcePatchNextDevStep"] = True


def reapply_force_patch_if_dev_stalled(task: Dict[str, Any]) -> bool:
    """Re-arm Forced Patch when the last Dev exit was explore/read-only stall."""
    from backend.services.sprint_speed_gates import (
        DEV_STALL_FORCE_PATCH_EXITS,
        last_step_exit_reason,
    )

    if last_step_exit_reason(task) in DEV_STALL_FORCE_PATCH_EXITS:
        apply_read_only_no_edits_outcome(task)
        return True
    return bool(task.get("forcePatchNextDevStep"))


def _outcome_stop_reason(
    *,
    agent_result: Optional[str],
    lane_before: str,
    lane_after: str,
    tools_used: Optional[set[str]] = None,
) -> str:
    from backend.services.step_diagnostics import derive_exit_reason, get_active_trace

    trace = get_active_trace()
    tools = tools_used if tools_used is not None else (trace.tools_used if trace else set())
    return derive_exit_reason(
        agent_result=agent_result,
        tools_used=tools,
        lane_before=lane_before,
        lane_after=lane_after,
    )


def _provisional_dev_exit_reason(
    agent_result: Optional[str],
    *,
    lane_before: str,
) -> str:
    """Exit reason before any lane move — used to block unhealthy QA/CR promotion."""
    from backend.services.sprint_speed_gates import provisional_dev_exit_reason
    from backend.services.step_diagnostics import get_active_trace

    trace = get_active_trace()
    tools = set(trace.tools_used) if trace else set()
    return provisional_dev_exit_reason(
        agent_result=agent_result,
        tools_used=tools,
        lane_before=lane_before,
        lane_after=lane_before,
    )


def _dev_unhealthy_exit_blocks_advance(exit_reason: Optional[str]) -> bool:
    from backend.services.sprint_speed_gates import unhealthy_exit_blocks_lane_advance
    from backend.services.step_diagnostics import _write_tools_succeeded, get_active_trace

    writes = 0
    trace = None
    try:
        trace = get_active_trace()
        if trace:
            writes = int(_write_tools_succeeded(getattr(trace, "tools_log", None) or []) or 0)
    except Exception:
        writes = 0
    lint_clean = bool(getattr(state, "FIX_VERIFY_LINT_CLEAN", False))
    verify_passed = False
    try:
        from backend.services.duplicate_tool_policy import verify_known_for_advance

        tools_log = getattr(trace, "tools_log", None) or [] if trace else []
        verify_passed = verify_known_for_advance(
            tools_log,
            getattr(state, "LAST_AGENT_STEP_RESULT", None),
        )
    except Exception:
        verify_passed = False
    return unhealthy_exit_blocks_lane_advance(
        exit_reason,
        writes_succeeded=writes,
        lint_clean=lint_clean,
        verify_passed=verify_passed,
    )


def _lint_file_hint(task: Optional[Dict[str, Any]], title: str) -> str:
    if isinstance(task, dict):
        try:
            from backend.services.file_blocker import _task_file_path

            path = _task_file_path(task)
            if path:
                return path
        except Exception:
            pass
        src = str(task.get("lintSourceFile") or "").strip()
        if src:
            return src
    if title.startswith("Lint: "):
        return title[6:].strip()[:120] or title
    return title


def _outcome_why_card_stayed(
    stop_reason: str,
    *,
    title: str,
    lane_after: str,
    plan_rejections: int = 0,
    text_rejections: int = 0,
    task: Optional[Dict[str, Any]] = None,
) -> str:
    if lane_after != "In Progress":
        return ""
    if stop_reason == "completed_with_writes":
        return ""
    if stop_reason == "lint_stay_in_progress":
        lint_file = _lint_file_hint(task, title)
        return (
            f"Lint still dirty on {lint_file} — auto sprint will run Forced Patch again."
        )
    if stop_reason == "tool_failure_stop" and task and stuck_is_tool_or_lint(task):
        lint_file = _lint_file_hint(task, title)
        return (
            f"apply_patch or verify failed on '{lint_file}' — lint diagnostics remain. "
            "Forced Patch will retry on the next auto sprint step."
        )
    base = (
        "Text responses are not executed as tools and are not added to the backlog or memory."
    )
    if stop_reason == "read_only_no_edits":
        return (
            f"Developer read files but never called apply_patch/write_file on '{title}'. {base}"
        )
    if stop_reason == "command_repeat_no_progress":
        return (
            f"Developer repeated the same successful run_command without verification or "
            f"lane move on '{title}'. Run lint/analyze/build next or escalate — do not loop clean/build. {base}"
        )
    if stop_reason == "plan_exhausted":
        return (
            f"Model returned plan-only text {plan_rejections} time(s) without calling apply_patch "
            f"on '{title}'. {base}"
        )
    if stop_reason == "max_iterations":
        return (
            f"Agent hit the LLM iteration limit on '{title}' without writing edits. {base}"
        )
    if stop_reason == "max_iterations_after_writes":
        return (
            f"Agent wrote files on '{title}' then stopped because verify was already known "
            "(duplicate skip) or the iteration cap was reached."
        )
    if stop_reason == "step_timeout":
        return (
            f"Agent step hit the wall-clock duration limit on '{title}' and stopped "
            f"to avoid an unbounded loop. {base}"
        )
    if stop_reason == "duplicate_tool":
        return (
            f"Agent repeated the same tool call on '{title}' and stopped (agent loop stop). "
            f"{base}"
        )
    if stop_reason == "completed_text_only":
        return (
            f"Developer returned text-only on '{title}' without apply_patch/write_file. {base}"
        )
    if stop_reason == "interrupted":
        return (
            f"Step was cancelled or raised an exception on '{title}' before completing. "
            "Do not treat this as a finished Developer turn."
        )
    if stop_reason == "identical_write_loop":
        return (
            f"The same successful patch was applied repeatedly on '{title}'. "
            "Stop rewriting; verify the files or split the card."
        )
    if text_rejections or plan_rejections:
        return (
            f"Step on '{title}' ended with {plan_rejections} plan and {text_rejections} text "
            f"rejections and no file edits. {base}"
        )
    return f"Card stayed in {lane_after} on '{title}'. {base}"


def _outcome_suggested_action(
    stop_reason: str,
    lane_after: str,
    *,
    task: Optional[Dict[str, Any]] = None,
) -> str:
    if lane_after != "In Progress":
        return ""
    if stop_reason == "completed_with_writes":
        return ""
    if stop_reason in ("lint_stay_in_progress", "tool_failure_stop"):
        return "Forced Patch queued — auto sprint will retry Developer on this card."
    if task and task.get("forcePatchNextDevStep") and is_lint_wall_card(task):
        return "Forced Patch queued — auto sprint will retry Developer on this card."
    if stop_reason == "identical_write_loop":
        return "Do not rewrite the same file. Verify, split the card, or edit manually."
    if stop_reason == "read_only_no_edits":
        return (
            "Next Developer step is Forced Patch — call apply_patch/write_file "
            "(scaffold first if the workspace has no source files)."
        )
    if stop_reason in ("explore_budget_exhausted", "duplicate_tool"):
        return (
            "Next Developer step is Forced Patch — call apply_patch/write_file "
            "(do not re-read docs/tasks; scaffold or patch lib/ now)."
        )
    if stop_reason == "phase_cycle_cap":
        return "Split the card or reset the Developer visit latch — Auto Sprint will not retry Dev."
    if stop_reason == "text_rejection_loop":
        target = ""
        if task:
            try:
                from backend.services.file_blocker import _task_file_path

                target = str(_task_file_path(task) or "").strip()
            except Exception:
                target = str(task.get("lintSourceFile") or "").strip()
        file_hint = f" on {target}" if target else ""
        return (
            f"Model refused tools{file_hint}; try backup model, manual edit, or split the card."
        )
    return "Run In Progress again or edit the workspace files manually, then move the card to QA."


def _build_last_step_outcome(
    task_id: str,
    lane_before: str,
    agent: str,
    *,
    agent_result: Optional[str] = None,
    park_attempted: bool = False,
    park_succeeded: bool = False,
    needs_user_kind: str = "",
) -> Dict[str, Any]:
    from backend.services.step_diagnostics import get_active_trace

    task = find_task_by_id(task_id)
    lane_after = get_task_lane(task_id) or lane_before
    card_tool_failures = _count_task_tool_failures(task) if task else 0
    title = str(task.get("title", task_id)) if task else task_id
    trace = get_active_trace()
    step_tool_failures = trace.tool_failures if trace else card_tool_failures
    tool_failures = step_tool_failures
    plan_rejections = trace.plan_rejections if trace else 0
    text_rejections = trace.text_rejections if trace else 0
    tools_used = sorted(trace.tools_used) if trace else []
    agent_snippet = (agent_result or "")[:200] if agent_result is not None else ""
    if not agent_snippet and agent_result is None:
        agent_snippet = (state.LAST_AGENT_STEP_RESULT or "")[:200]
    if trace and not agent_snippet and agent_result is None and trace.events:
        for event in reversed(trace.events):
            if event.get("kind") in ("plan_rejected", "text_rejected"):
                agent_snippet = str(event.get("message", ""))[:200]
                break

    stop_reason = _outcome_stop_reason(
        agent_result=agent_result,
        lane_before=lane_before,
        lane_after=lane_after,
    )
    why_card_stayed = _outcome_why_card_stayed(
        stop_reason,
        title=title,
        lane_after=lane_after,
        plan_rejections=plan_rejections,
        text_rejections=text_rejections,
        task=task,
    )
    suggested_action = _outcome_suggested_action(stop_reason, lane_after, task=task)
    model_response_type = "text_only" if agent_result and not (trace and trace.tools_used & {"write_file", "apply_patch"}) else None
    if agent_result and agent_result.startswith("Max tool iterations"):
        model_response_type = "text_only"

    ok = True
    message = f"Step completed on '{title}'."
    if agent_result:
        if agent_result == "SIMULATION_FALLBACK":
            ok = False
            message = f"Ollama unavailable — simulation fallback on '{title}'."
        elif (
            agent_result.startswith("Stopped:")
            or agent_result.startswith("Max tool iterations")
            or agent_result.startswith("Timed out:")
        ):
            ok = False
            message = f"{agent_result[:200]}"
    if stop_reason in ("po_clarification_incomplete", "po_generation_truncated"):
        ok = False
        if not (agent_result or "").startswith("Stopped:"):
            message = f"PO clarification did not leave Needs PO on '{title}'."
    if tool_failures > 0:
        ok = False
        message = (
            f"Step finished with {tool_failures} tool failure(s) on '{title}'. "
            f"Card still in {lane_after}. Open the card → Transcript or Tools tab."
        )
    elif state.DEV_STEP_READ_ONLY_NO_EDITS:
        ok = False
        message = (
            f"Dev step read files but made no edits on '{title}'. "
            f"Card still in {lane_after}. Open the card → Transcript or Tools tab."
        )
    elif state.DEV_STEP_COMMAND_REPEAT_NO_PROGRESS:
        ok = False
        message = (
            f"Dev step repeated the same command without progress on '{title}'. "
            f"Card still in {lane_after}. Run verification or escalate — see Transcript."
        )
    elif lane_before != lane_after:
        message = f"'{title}' moved from {lane_before} → {lane_after}."
    elif lane_before == lane_after and lane_after == "In Progress":
        ok = False if why_card_stayed else True
        if why_card_stayed:
            message = f"Card stayed In Progress: {why_card_stayed}"
        else:
            message = f"Dev step finished on '{title}' — card still In Progress."

    if agent == "Product Owner":
        left_needs_po = lane_before == "Needs PO" and lane_after != "Needs PO"
        if stop_reason == "po_clarified" and left_needs_po:
            ok = True
            message = f"PO clarification applied on '{title}' ({lane_before} → {lane_after})."
            if task and task.get("forcePatchNextDevStep"):
                suggested_action = (
                    "Next Developer step is Forced Patch — call apply_patch/write_file "
                    "(scaffold first if the workspace has no source files)."
                )

    if state.DEV_STEP_INTERRUPTED or state.SPRINT_CANCEL:
        stop_reason = "interrupted"
        ok = False
        message = (
            f"Step was cancelled or raised an exception on '{title}' before completing."
        )
        why_card_stayed = _outcome_why_card_stayed(
            stop_reason,
            title=title,
            lane_after=lane_after,
            plan_rejections=plan_rejections,
            text_rejections=text_rejections,
        )
        suggested_action = _outcome_suggested_action(stop_reason, lane_after)

    if stop_reason == "max_iterations_after_writes":
        ok = True
        if agent_result:
            message = str(agent_result)[:200]

    outcome: Dict[str, Any] = {
        "taskId": task_id,
        "agent": agent,
        "laneBefore": lane_before,
        "laneAfter": lane_after,
        "toolFailures": tool_failures,
        "cardToolFailures": card_tool_failures,
        "ok": ok,
        "message": message,
        "stopReason": stop_reason,
        "exitReason": stop_reason,
        "planRejections": plan_rejections,
        "textRejections": text_rejections,
        "toolsUsed": tools_used,
    }
    if why_card_stayed:
        outcome["whyCardStayed"] = why_card_stayed
    if suggested_action:
        outcome["suggestedAction"] = suggested_action
    if agent_snippet:
        outcome["agentResultSnippet"] = agent_snippet
    if model_response_type:
        outcome["modelResponseType"] = model_response_type
    if task and task.get("forcePatchNextDevStep"):
        outcome["forcePatchNextDevStep"] = True
    if park_attempted:
        outcome["parkAttempted"] = True
        outcome["parkSucceeded"] = bool(park_succeeded)
    if needs_user_kind:
        outcome["needsUserKind"] = str(needs_user_kind)[:40]
    route_reason = str(getattr(state, "LAST_MODEL_ROUTE_REASON", "") or "").strip()
    if route_reason:
        outcome["modelRouteReason"] = route_reason
    try:
        from backend.services.prompt_budget import describe_num_ctx_clamp

        fit = describe_num_ctx_clamp(agent)
        if fit:
            outcome["numCtxFit"] = fit
            if fit.get("label"):
                outcome["numCtxLabel"] = fit["label"]
    except Exception:
        pass
    progress: Optional[Dict[str, Any]] = None
    if agent == "Product Owner":
        if suggested_action or why_card_stayed:
            progress = {"taskId": task_id}
            if why_card_stayed:
                progress["whyCardStayed"] = why_card_stayed
            if suggested_action:
                progress["suggestedAction"] = suggested_action
    else:
        progress = state.LAST_STEP_PROGRESS if isinstance(state.LAST_STEP_PROGRESS, dict) else None
        if isinstance(progress, dict) and str(progress.get("taskId") or "") != str(task_id):
            progress = None
        if not progress and task and isinstance(task.get("lastStepProgress"), dict):
            progress = task["lastStepProgress"]
        if not progress and stop_reason == "max_iterations":
            from backend.services.step_diagnostics import build_step_progress

            progress = build_step_progress(
                task_id=task_id,
                iterations_used=(trace.llm_iterations_used if trace else 0),
                iterations_max=(trace.llm_iterations_max if trace else 0),
                tools_used=set(tools_used) if tools_used else None,
            )
    if progress:
        from backend.services.step_diagnostics import (
            prefer_richer_phase_graph,
            store_step_progress,
        )

        progress = dict(progress)
        if why_card_stayed:
            progress["whyCardStayed"] = why_card_stayed
        if suggested_action:
            progress["suggestedAction"] = suggested_action
        if stop_reason == "max_iterations":
            suggested_action = (
                "Extend the step (+4/+8 iterations) to continue with context from what already ran, "
                "or Reset & retry for a fresh step."
            )
            progress["suggestedAction"] = suggested_action
            outcome["suggestedAction"] = suggested_action
        # Do not let a stale Cycle 1 blob overwrite a richer graph already on the task.
        if task and isinstance(task.get("lastStepProgress"), dict):
            existing_g = (task["lastStepProgress"] or {}).get("devPhaseGraph")
            incoming_g = progress.get("devPhaseGraph")
            chosen = prefer_richer_phase_graph(
                incoming_g if isinstance(incoming_g, dict) else None,
                existing_g if isinstance(existing_g, dict) else None,
            )
            if chosen:
                progress["devPhaseGraph"] = chosen
                if chosen.get("label"):
                    progress["devPhase"] = chosen.get("label")
        store_step_progress(progress)
        outcome["stepProgress"] = progress
    return outcome


def _compact_last_step_outcome_for_task(outcome: Dict[str, Any]) -> Dict[str, Any]:
    """Persist a prompt-friendly slice of the last step outcome on the task."""
    tools = outcome.get("toolsUsed") or []
    if isinstance(tools, list):
        tools_short = [str(t) for t in tools[:12]]
    else:
        tools_short = []
    compact: Dict[str, Any] = {
        "stopReason": outcome.get("stopReason") or outcome.get("exitReason"),
        "exitReason": outcome.get("exitReason") or outcome.get("stopReason"),
        "message": str(outcome.get("message") or "")[:240],
        "whyCardStayed": str(outcome.get("whyCardStayed") or "")[:400],
        "suggestedAction": str(outcome.get("suggestedAction") or "")[:240],
        "toolsUsed": tools_short,
        "ok": outcome.get("ok"),
        "agent": outcome.get("agent"),
        "laneBefore": outcome.get("laneBefore"),
        "laneAfter": outcome.get("laneAfter"),
    }
    if outcome.get("modelRouteReason"):
        compact["modelRouteReason"] = outcome.get("modelRouteReason")
    if outcome.get("parkAttempted"):
        compact["parkAttempted"] = True
        compact["parkSucceeded"] = bool(outcome.get("parkSucceeded"))
    if outcome.get("needsUserKind"):
        compact["needsUserKind"] = outcome.get("needsUserKind")
    return {k: v for k, v in compact.items() if v not in (None, "", [], {})}


def _interrupted_outcome_already_recorded(task_id: str) -> bool:
    outcome = state.LAST_STEP_OUTCOME if isinstance(state.LAST_STEP_OUTCOME, dict) else {}
    return (
        str(outcome.get("taskId") or "") == str(task_id)
        and str(outcome.get("exitReason") or outcome.get("stopReason") or "") == "interrupted"
    )


def _ensure_interrupted_step_recorded(task_id: str, lane_before: str, agent: str) -> None:
    """Persist exitReason=interrupted so auto-sprint watchdogs see the crash, not a stale PO outcome."""
    state.DEV_STEP_INTERRUPTED = True
    if _interrupted_outcome_already_recorded(task_id):
        return
    try:
        _record_last_step_outcome(task_id, lane_before, agent)
    except Exception:
        pass
    try:
        agent_key = {
            "Developer": "dev",
            "Product Owner": "po",
            "Code Reviewer": "cr",
            "QA Tester": "qa",
        }.get(str(agent or ""), "dev")
        _check_stuck_and_escalate(task_id, lane_before, agent_key=agent_key)
    except Exception:
        pass


def _record_last_step_outcome(
    task_id: str,
    lane_before: str,
    agent: str,
    *,
    agent_result: Optional[str] = None,
    skip_next_work_visit: bool = False,
    park_attempted: bool = False,
    park_succeeded: bool = False,
    needs_user_kind: str = "",
) -> None:
    state.LAST_STEP_OUTCOME = _build_last_step_outcome(
        task_id,
        lane_before,
        agent,
        agent_result=agent_result or state.LAST_AGENT_STEP_RESULT,
        park_attempted=park_attempted,
        park_succeeded=park_succeeded,
        needs_user_kind=needs_user_kind,
    )
    task = find_task_by_id(task_id)
    if task and isinstance(state.LAST_STEP_OUTCOME, dict):
        normalize_task(task)
        task["lastStepOutcome"] = _compact_last_step_outcome_for_task(state.LAST_STEP_OUTCOME)
        try:
            from backend.agents.tool_fingerprints import finalize_step_tool_fingerprints

            stop = str(
                state.LAST_STEP_OUTCOME.get("stopReason")
                or state.LAST_STEP_OUTCOME.get("exitReason")
                or ""
            )
            step_keys = list(getattr(state, "STEP_TOOL_FINGERPRINT_KEYS", None) or [])
            block_keys = list(getattr(state, "STEP_TOOL_BLOCK_KEYS", None) or [])
            finalize_step_tool_fingerprints(
                task,
                step_keys,
                stop_reason=stop,
                block_keys=block_keys,
            )
        except Exception:
            pass
        try:
            from backend.services.agent_work_items import refresh_agent_work_items

            refresh_agent_work_items(task)
        except Exception:
            pass
        if str(agent or "") == "Developer" and not skip_next_work_visit:
            try:
                from backend.services.card_ledger import last_oracle_passed, record_next_work_visit
                from backend.services.step_diagnostics import _write_tools_succeeded, get_active_trace

                writes = 0
                trace = get_active_trace()
                if trace and _write_tools_succeeded(getattr(trace, "tools_log", None) or []):
                    writes = 1
                record_next_work_visit(
                    task,
                    writes_succeeded=writes,
                    oracle_passed=last_oracle_passed(task_id, task),
                )
            except Exception:
                pass
        # Persist a thin diagnostics pointer when available after finalize below.
    _finalize_step_diagnostics_if_traced(task_id)
    if task and isinstance(state.LAST_STEP_DIAGNOSTICS, dict):
        diag = state.LAST_STEP_DIAGNOSTICS
        task["lastStepDiagnostics"] = {
            "exitReason": diag.get("exitReason"),
            "durationMs": diag.get("durationMs"),
            "ollamaMsTotal": diag.get("ollamaMsTotal"),
            "toolMsTotal": diag.get("toolMsTotal"),
            "planRejections": diag.get("planRejections"),
            "textRejections": diag.get("textRejections"),
            "toolsUsed": diag.get("toolsUsed") or [],
            "filePath": diag.get("filePath"),
            "ok": diag.get("ok"),
            "ollamaCallCount": diag.get("ollamaCallCount")
            or len(diag.get("ollamaCalls") or []),
            "fixVerifyLintClean": diag.get("fixVerifyLintClean"),
            "writesSucceeded": diag.get("writesSucceeded"),
        }
        try:
            from backend.services.sprint_speed_gates import record_consecutive_bad_exit

            exit_r = str(
                diag.get("exitReason")
                or (state.LAST_STEP_OUTCOME or {}).get("exitReason")
                or ""
            )
            lane_after = get_task_lane(task_id) or lane_before
            focus_completed = bool(
                task.get("focusSliceCompletedAt")
                or task.get("lastFocusSliceCompletedAt")
            )
            record_consecutive_bad_exit(
                task,
                exit_r,
                progress_made=lane_after != lane_before or focus_completed,
                writes_succeeded=int(diag.get("writesSucceeded") or 0),
            )
        except Exception:
            pass
    state.DEV_STEP_READ_ONLY_NO_EDITS = False
    state.DEV_STEP_COMMAND_REPEAT_NO_PROGRESS = False
    state.DEV_STEP_INTERRUPTED = False


def _finalize_step_diagnostics_if_traced(task_id: str) -> None:
    from backend.services.step_diagnostics import finalize_active_step_trace

    lane_after = get_task_lane(task_id) or ""
    finalize_active_step_trace(lane_after=lane_after)


def _ensure_dev_step_trace(task_id: str, task_title: str, lane_before: str) -> None:
    _ensure_step_trace(task_id, task_title, "Developer", lane_before)


def _forced_patch_retry_allowed(task: Dict[str, Any]) -> bool:
    """True when a card should bypass dev pre-check parks and run Forced Patch."""
    from backend.services.card_ledger import should_bypass_same_next_task_park

    return should_bypass_same_next_task_park(task)


def _record_dev_precheck_skip(
    task_id: str,
    title: str,
    lane_before: str,
    *,
    reason: str,
    park_attempted: bool = False,
    park_succeeded: bool = False,
    needs_user_kind: str = "",
) -> None:
    """Write step diagnostics when Developer is skipped in a pre-check (no LLM call)."""
    _ensure_dev_step_trace(task_id, title, lane_before)
    state.LAST_AGENT_STEP_RESULT = reason
    with state.STATE_LOCK:
        _record_last_step_outcome(
            task_id,
            lane_before,
            "Developer",
            agent_result=reason,
            skip_next_work_visit=True,
            park_attempted=park_attempted,
            park_succeeded=park_succeeded,
            needs_user_kind=needs_user_kind,
        )


def _ensure_step_trace(
    task_id: str,
    task_title: str,
    agent: str,
    lane_before: str,
) -> None:
    from backend.services.step_diagnostics import get_active_trace, start_step_trace

    if get_active_trace() is None:
        start_step_trace(task_id, task_title, agent, lane_before)


def _finalize_role_step_diagnostics(
    task_id: str,
    lane_before: str,
    agent: str,
    *,
    agent_result: Optional[str] = None,
) -> None:
    """Record outcome + finalize step JSON for any sprint role (Dev/PO/CR/QA)."""
    try:
        _record_last_step_outcome(
            task_id, lane_before, agent, agent_result=agent_result
        )
    except Exception:
        pass


def _start_sprint_session(
    handler: str,
    active_task: Dict[str, Any],
    *,
    sprint_mode: Optional[str] = None,
) -> None:
    from backend.services.sprint_session import set_sprint_mode, start_session
    from backend.services.step_diagnostics import get_active_trace

    if sprint_mode:
        set_sprint_mode(sprint_mode)  # type: ignore[arg-type]
    task_id = str(active_task.get("id", ""))
    lane = get_task_lane(task_id) or str(active_task.get("status", ""))
    agent = _HANDLER_AGENT.get(handler, "System")
    trace = get_active_trace()
    diag_path = str(trace.file_path) if trace else None
    start_session(
        task_id=task_id,
        task_title=str(active_task.get("title", task_id)),
        lane=lane,
        agent=agent,
        handler=handler,
        diagnostics_file=diag_path,
    )


def _finish_sprint_session(handler: Optional[str]) -> None:
    from backend.services.sprint_session import clear_session

    if handler and handler not in ("idle", "needs_user", "blocked"):
        clear_session("idle")


def _finalize_dev_step_diagnostics_if_auto_sprint(task_id: str, lane_before: str) -> None:
    if state.SPRINT_PROGRESS_MAX == 1:
        return
    _finalize_step_diagnostics_if_traced(task_id)


def _finish_single_step_progress(
    active_task: Optional[Dict[str, Any]],
    *,
    status: str = "done",
) -> None:
    if state.SPRINT_PROGRESS_MAX != 1:
        return
    task_id = str(active_task.get("id", "")) if active_task else ""
    task_title = str(active_task.get("title", "")) if active_task else ""
    lane = get_task_lane(task_id) if task_id else ""
    publish_sprint_progress(
        phase="done",
        step=1,
        max_steps=1,
        task_id=task_id,
        task_title=task_title,
        lane=lane or "",
        status=status,
    )
    state.SPRINT_PROGRESS_STEP = 0
    state.SPRINT_PROGRESS_MAX = int(get_workflow_settings().get("maxSprintSteps", 20))


def _po_chat_used_add_backlog_tool(task_id: str) -> bool:
    from backend.agents.task_context import find_task_by_id

    task = find_task_by_id(task_id)
    if not task:
        return False
    for entry in reversed(task.get("transcript") or []):
        if not isinstance(entry, dict):
            continue
        if entry.get("toolName") == "add_backlog_tasks" and entry.get("toolSuccess") is not False:
            return True
    return False


def apply_backlog_from_po_response(response: str, task_id: str) -> int:
    """Apply JSON subtasks from PO chat when the model embeds an array instead of calling add_backlog_tasks."""
    from backend.agents.task_context import find_task_by_id

    if not task_id or not find_task_by_id(task_id):
        return 0
    if _po_chat_used_add_backlog_tool(task_id):
        return 0
    try:
        parsed = extract_json_array_from_text(response)
    except ValueError:
        return 0
    valid = [t for t in parsed if t.get("title") and t.get("description")]
    if not valid:
        return 0
    existing = existing_backlog_titles()
    new_tasks = [t for t in valid if t.get("title") not in existing]
    if not new_tasks:
        return 0
    parent = find_task_by_id(task_id) or {}
    for child in new_tasks:
        for field in ("scope", "outOfScope", "testPlan", "userStory"):
            if not child.get(field) and parent.get(field):
                child[field] = parent[field]
        child.setdefault("workType", "implementation")
        child.setdefault("requiresDev", True)
        child.setdefault("requiresQa", True)
    append_backlog_tasks(new_tasks, split_from_task_id=task_id)
    add_system_log(
        "Product Owner",
        "success",
        f"PO chat added {len(new_tasks)} subtask(s) from JSON (split from {task_id})",
    )
    return len(new_tasks)


def extract_json_array_from_text(text: str) -> List[Dict[str, Any]]:
    bt = "```"
    json_blocks = re.findall(rf"{bt}json\s*(.*?)\s*{bt}", text, re.DOTALL)
    for block in json_blocks:
        try:
            parsed = json.loads(block.strip())
            if isinstance(parsed, list):
                return parsed
        except json.JSONDecodeError:
            continue
    try:
        parsed = json.loads(text.strip())
        if isinstance(parsed, list):
            return parsed
    except json.JSONDecodeError:
        pass
    match = re.search(r"\[.*\]", text, re.DOTALL)
    if match:
        try:
            parsed = json.loads(match.group())
            if isinstance(parsed, list):
                return parsed
        except json.JSONDecodeError:
            pass
    raise ValueError("No valid JSON task array found in LLM output")


def extract_json_object_from_text(text: str) -> Optional[Dict[str, Any]]:
    from backend.services.po_clarification import extract_json_object_from_text as _extract

    return _extract(text)


def _task_in_lane(task_id: str, lane: str) -> bool:
    needle = str(task_id)
    return needle in [str(t.get("id", "")) for t in state.SHARED_BOARD.get(lane, [])]


def _dev_needs_po(result: str, task: Optional[Dict[str, Any]] = None) -> bool:
    """Stricter escalation detection — avoid substring false positives."""
    from backend.services.workflow_settings import get_execution_profile

    if get_execution_profile() == "implementer":
        return False
    if task:
        normalize_task(task)
        ac = task.get("acceptanceCriteria") or []
        if ac and task.get("poRoundTrips", 0) > 0:
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
        if stripped.startswith("blocked on requirements:"):
            return True
    return False


def _autonomous_mode_active() -> bool:
    return bool(get_workflow_settings().get("autonomousMode"))


def _autonomous_instruction_suffix() -> str:
    base = prefer_po_instruction_suffix()
    if not _autonomous_mode_active():
        return base
    return (
        base
        + " Autonomous mode: act without asking the user when acceptance criteria exist; "
        "only escalate to Needs User for true user-only decisions (secrets, irreversible design)."
    )


def _needs_user_cap_reached() -> bool:
    if not _autonomous_mode_active():
        return False
    cap = int(get_workflow_settings().get("maxNeedsUserPerSprint", 2))
    return state.SPRINT_NEEDS_USER_COUNT >= cap


def _set_needs_user_fields(task: Dict[str, Any], msg: str, *, kind: str = "stuck_loop") -> None:
    """Populate structured Needs User question / why / how-to-unblock fields."""
    brief = build_needs_user_brief(task, kind=kind, raw_msg=msg)
    apply_needs_user_brief(task, brief)


def _redirect_to_needs_po(task_id: str, task: Dict[str, Any], msg: str, *, kind: str = "clarification") -> bool:
    """Route clarification-shaped escalations to Needs PO instead of Needs User."""
    if _escalate_po_limit(task):
        add_system_log(
            "System",
            "warning",
            f"{task_id}: clarification blocked — PO limit reached; staying in lane",
        )
        return False
    increment_po_round_trips(task_id)
    move_board_stage(task_id, "Needs PO")
    publish_activity(
        task_id,
        kind,
        msg[:500],
        role="system",
        agent="System",
        lane="Needs PO",
    )
    add_system_log("System", "info", f"{task_id}: routed to Needs PO (not Needs User): {msg[:120]}")
    try:
        from backend.services.phone_notify import notify_if_enabled

        title = task.get("title") or task_id
        notify_if_enabled(
            "needs_po",
            "Needs PO",
            f"{title}\n{msg[:400]}",
            task_id=task_id,
        )
    except Exception:
        pass
    return True


def _reroute_tool_blocker_instead_of_needs_user(
    task_id: str,
    task: Dict[str, Any],
    reason: str,
) -> bool:
    reroute_tool_blocker_to_dev(task_id, task, reason)
    if is_lint_wall_card(task):
        _keep_lint_card_for_developer(task)
    return False


def _live_board_task(task_id: str) -> Optional[Dict[str, Any]]:
    from backend.agents.task_context import find_task_by_id

    return find_task_by_id(str(task_id or ""))


def _handle_visit_cap_stall(
    task_id: str,
    task: Dict[str, Any],
    reason: str,
    *,
    title: str,
    lane_before: str,
    brief: str,
) -> None:
    """Auto-split or recover first; Needs User with MCQ only when visit cap requires it."""
    del brief
    live = _live_board_task(task_id) or task
    if is_lint_wall_card(live):
        _handle_lint_stuck(task_id, live, reason)
        _record_dev_precheck_skip(task_id, title, lane_before, reason=reason)
        return

    ws = get_workflow_settings()
    max_stuck = int(ws.get("maxStuckSteps", 3) or 3)
    if _should_attempt_stuck_auto_split(live, ws):
        if _run_stuck_auto_split(task_id, live, max_stuck):
            _record_dev_precheck_skip(task_id, title, lane_before, reason=reason)
            return

    live = _live_board_task(task_id) or live
    if live.get("phaseCycleCapReached"):
        parked = _try_move_to_needs_user(task_id, live, reason, kind="phase_cycle_cap")
        _record_dev_precheck_skip(
            task_id,
            title,
            lane_before,
            reason=reason,
            park_attempted=True,
            park_succeeded=parked,
            needs_user_kind="phase_cycle_cap" if parked else "",
        )
        return

    reroute_tool_blocker_to_dev(task_id, live, reason)
    _record_dev_precheck_skip(task_id, title, lane_before, reason=reason)


def _try_move_to_needs_user(
    task_id: str,
    task: Dict[str, Any],
    msg: str,
    *,
    kind: str = "stuck_loop",
) -> bool:
    live = _live_board_task(task_id)
    if not live:
        add_system_log("System", "warning", f"{task_id}: Needs User park failed — task not on board")
        return False
    allowed, block_reason = should_escalate_to_needs_user(live, msg, kind=kind)
    if not allowed:
        if block_reason == "clarification_use_po" and kind != "phase_cycle_cap":
            max_po = int(get_workflow_settings().get("maxPoRoundTrips", 3))
            if int(live.get("poRoundTrips") or 0) < max_po:
                return _redirect_to_needs_po(task_id, live, msg, kind=kind)
            if stuck_is_tool_or_lint(live):
                return _reroute_tool_blocker_instead_of_needs_user(
                    task_id, live, "po_exhausted_tool_blocker"
                )
            allowed = True
        elif block_reason == "lint_use_file_blocker":
            return _reroute_tool_blocker_instead_of_needs_user(task_id, live, block_reason)
        elif block_reason in (
            "duplicate_question",
            "cooldown_active",
            "same_reason_hash",
            "already_in_needs_user",
        ):
            add_system_log(
                "System",
                "warning",
                f"{task_id}: Needs User blocked ({block_reason}) — {msg[:120]}",
            )
        if not allowed:
            return False
    if kind != "phase_cycle_cap" and _needs_user_cap_reached():
        add_system_log(
            "System",
            "warning",
            f"{task_id}: Needs User blocked by autonomous cap ({state.SPRINT_NEEDS_USER_COUNT}) — {msg[:120]}",
        )
        return False
    brief = build_needs_user_brief(live, kind=kind, raw_msg=msg)
    park_ok, park_reason = should_park_in_needs_user(live, brief)
    if not park_ok:
        return _reroute_tool_blocker_instead_of_needs_user(task_id, live, park_reason)
    apply_needs_user_brief(live, brief)
    try:
        enrich_needs_user_options(live)
    except Exception:
        pass
    activity_msg = str(brief.get("question") or msg)
    move_result = move_board_stage(
        task_id,
        "Needs User",
        park_kind=kind,
        park_msg=msg,
    )
    if not str(move_result).startswith("Successfully"):
        clear_needs_user_fields(live)
        add_system_log(
            "System",
            "warning",
            f"{task_id}: Needs User park failed — {move_result[:200]}",
        )
        return False
    state.SPRINT_NEEDS_USER_COUNT += 1
    publish_activity(
        task_id,
        kind,
        activity_msg,
        role="system",
        agent="System",
        lane="Needs User",
    )
    add_system_log("System", "warning", f"{task_id}: {activity_msg}")
    try:
        from backend.services.phone_notify import notify_if_enabled

        title = task.get("title") or task_id
        reason = task.get("needsUserReason") or msg
        question = str(task.get("userQuestion") or "").strip()
        action = str(task.get("needsUserAction") or "").strip()
        body_parts = [f"[{task_id}] {title}"]
        if question:
            body_parts.append(f"Question: {question[:350]}")
        if reason and reason != question:
            body_parts.append(str(reason)[:350])
        if action:
            body_parts.append(f"How to unblock: {action[:250]}")
        body_parts.append("Reply with /ah-answer or use the UI.")
        notify_if_enabled(
            "needs_user",
            "Needs your answer",
            "\n".join(body_parts),
            task_id=task_id,
        )
    except Exception:
        pass
    return True


def _split_ollama_url(explicit: str = "") -> str:
    url = str(explicit or "").strip()
    if url:
        return url
    return (
        str(getattr(agent_dev, "ollama_url", "") or "").strip()
        or str(getattr(agent_po, "ollama_url", "") or "").strip()
        or "http://localhost:11434"
    )


def queue_pending_split(task_id: str, guidance: str = "", requested_by: str = "ui") -> Dict[str, Any]:
    """Mark a card to be split after the current agent step finishes."""
    task = find_task_by_id(task_id)
    if not task:
        raise ValueError(f"Task not found: {task_id}")
    normalize_task(task)
    pending = {
        "requestedAt": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "guidance": str(guidance or ""),
        "requestedBy": str(requested_by or "ui"),
    }
    task["pendingSplit"] = pending
    record_task_decision(
        task_id,
        "System",
        "split_queued",
        "Split queued — will run after the current sprint step",
        pending["guidance"][:400],
    )
    add_system_log(
        "System",
        "info",
        f"{task_id}: split queued until the current sprint step finishes",
    )
    save_current_project_state()
    publish_board_update(task_id, source="split_queued")
    return pending


def collect_pending_split_task_ids() -> List[str]:
    ids: List[str] = []
    board = state.SHARED_BOARD or {}
    for lane, tasks in board.items():
        if lane == "Done":
            continue
        for task in tasks or []:
            if not isinstance(task, dict):
                continue
            if task.get("pendingSplit") and task.get("id"):
                ids.append(str(task["id"]))
    return ids


def drain_pending_splits(ollama_url: str = "") -> List[Dict[str, Any]]:
    """Run queued PO splits now that no agent step is in flight."""
    results: List[Dict[str, Any]] = []
    url = _split_ollama_url(ollama_url)
    for task_id in collect_pending_split_task_ids():
        task = find_task_by_id(task_id)
        if not task:
            continue
        if get_task_lane(task_id) == "Done":
            task["pendingSplit"] = None
            continue
        pending = task.get("pendingSplit") if isinstance(task.get("pendingSplit"), dict) else {}
        guidance = str((pending or {}).get("guidance") or "").strip() or (
            "Queued split after sprint step — break into 2–5 smallest cards."
        )
        task["pendingSplit"] = None
        add_system_log("System", "info", f"{task_id}: draining queued split")
        try:
            split_result = run_po_split_task(task_id, url, guidance=guidance)
            results.append({"taskId": task_id, "splitResult": split_result})
        except Exception as exc:
            add_system_log(
                "System",
                "warning",
                f"{task_id}: queued split failed ({exc})",
            )
            results.append({"taskId": task_id, "error": str(exc)[:400]})
    return results


LINT_LATCH_SPLIT_GUIDANCE = (
    "This is already a single analyzer finding — split into: (1) fix this diagnostic "
    "in the named file, (2) a regression test. Do not ask the user."
)


def is_visit_cap_needs_user_card(task: Dict[str, Any]) -> bool:
    if str(task.get("needsUserKind") or "") == "phase_cycle_cap":
        return True
    blob = " ".join(
        str(task.get(k) or "")
        for k in ("userQuestion", "needsUserReason", "needsUserAction")
    ).lower()
    return (
        "phase cycle cap" in blob
        or "visit latch" in blob
        or "developer visit cap" in blob
    )


def visit_cap_needs_user_task_ids(task_ids: Optional[List[str]] = None) -> List[str]:
    wanted = {str(tid).strip() for tid in (task_ids or []) if str(tid).strip()}
    found: List[str] = []
    for task in state.SHARED_BOARD.get("Needs User") or []:
        if not isinstance(task, dict):
            continue
        tid = str(task.get("id") or "")
        if not tid:
            continue
        if wanted and tid not in wanted:
            continue
        if is_visit_cap_needs_user_card(task):
            found.append(tid)
    return found


def split_visit_cap_batch(
    ollama_url: str = "",
    guidance: str = "",
    task_ids: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Split Needs User visit-cap cards now, or queue until the current step ends."""
    from backend.agents.agent_run import get_active_run

    ids = visit_cap_needs_user_task_ids(task_ids)
    url = _split_ollama_url(ollama_url)
    active = get_active_run() is not None
    results: List[Dict[str, Any]] = []
    queued_any = False
    for tid in ids:
        live = find_task_by_id(tid) or {}
        split_guidance = str(guidance or "").strip() or _stuck_auto_split_guidance(live)
        if active:
            queue_pending_split(tid, split_guidance, requested_by="ui-batch")
            queued_any = True
            results.append({"taskId": tid, "queued": True, "added": 0})
            continue
        try:
            split_result = run_po_split_task(tid, url, split_guidance)
            results.append({**(split_result or {}), "queued": False})
        except Exception as exc:
            results.append({"taskId": tid, "queued": False, "added": 0, "error": str(exc)[:400]})
    return {
        "queued": queued_any,
        "taskIds": ids,
        "results": results,
        "splitResult": {
            "added": sum(int(r.get("added") or 0) for r in results),
            "taskIds": ids,
            "queued": queued_any,
        },
    }


def _stuck_auto_split_guidance(task: Dict[str, Any]) -> str:
    title = str(task.get("title") or "")
    if stuck_is_tool_or_lint(task) or title.startswith("Lint:"):
        return LINT_LATCH_SPLIT_GUIDANCE
    return (
        "Auto-split: agents stuck after backup model attempts — "
        "break into 2–5 smallest cards."
    )


def _should_attempt_stuck_auto_split(task: Dict[str, Any], ws: Dict[str, Any]) -> bool:
    if not ws.get("enableSplitOnStuck", True):
        return False
    if task.get("splitAttemptedOnStuck"):
        return False
    if task.get("pendingSplit"):
        return False
    from backend.services.sprint_speed_gates import last_step_exit_reason, stuck_is_explore_without_write

    # Visit-cap latch: split even for lint/tool fanout (unlatched lint still skips below).
    if task.get("phaseCycleCapReached"):
        return True
    if stuck_is_tool_or_lint(task):
        return False
    if task.get("forcePatchAttempted"):
        return True
    exit_r = last_step_exit_reason(task)
    if exit_r in ("patch_budget_exhausted", "max_iterations", "tool_failure_stop"):
        return True
    if stuck_is_explore_without_write(task):
        return False
    return True


def _run_stuck_auto_split(task_id: str, task: Dict[str, Any], max_stuck: int) -> bool:
    """Attempt PO auto-split. Return True if the parent was superseded."""
    task["splitAttemptedOnStuck"] = True
    record_task_decision(
        task_id,
        "System",
        "stuck_split",
        "Auto-split after stuck steps (backup tried first)",
        f"stuckLoops reached {max_stuck} — attempting PO split before Needs PO",
    )
    try:
        split_result = run_po_split_task(
            task_id,
            _split_ollama_url(),
            guidance=_stuck_auto_split_guidance(task),
        )
        added = int((split_result or {}).get("added") or 0)
        lane_now = get_task_lane(task_id)
        split_parent = find_task_by_id(task_id)
        split_succeeded = bool(
            added > 0
            and lane_now == "Done"
            and split_parent
            and split_parent.get("splitSuperseded")
        )
        if split_succeeded:
            try:
                from backend.services.backup_model import (
                    clear_backup_remaining,
                    restore_primary_model,
                )

                fresh = find_task_by_id(task_id)
                if fresh:
                    clear_backup_remaining(fresh)
                restore_primary_model(agent_po, "po")
                restore_primary_model(agent_dev, "dev")
                restore_primary_model(agent_cr, "cr")
                restore_primary_model(agent_qa, "qa")
            except Exception:
                pass
            add_system_log(
                "System",
                "success",
                f"{task_id}: stuck recovery — auto-split added {added} card(s); skipping Needs PO",
            )
            return True
        add_system_log(
            "System",
            "warning",
            f"{task_id}: stuck auto-split did not supersede parent — escalating to Needs PO",
        )
    except Exception as exc:
        record_task_decision(
            task_id,
            "System",
            "stuck_split",
            "Auto-split failed — escalating to Needs PO",
            str(exc)[:500],
        )
        add_system_log(
            "System",
            "warning",
            f"{task_id}: stuck auto-split failed ({exc}) — escalating to Needs PO",
        )
    return False


def _check_stuck_and_escalate(
    task_id: str,
    lane_before: str,
    *,
    agent_key: Optional[str] = None,
) -> None:
    """Escalate when a sprint step completes without moving the card."""
    task = find_task_by_id(task_id)
    if not task:
        return
    normalize_task(task)
    lane_after = get_task_lane(task_id) or lane_before

    if lane_before != lane_after:
        task["stuckLoops"] = 0
        task.pop("autoExtendUsed", None)
        task.pop("lastStepOutcome", None)
        delivery_progress = lane_after in ("Code Review", "QA", "Done")
        if delivery_progress:
            task["consecutiveBadExits"] = 0
            task.pop("lastCircuitExitReason", None)
            task.pop("cloudRecoveryAttempted", None)
            try:
                from backend.services.sprint_speed_gates import clear_patch_fingerprint

                clear_patch_fingerprint(task)
            except Exception:
                pass
        try:
            from backend.agents.tool_fingerprints import clear_fingerprint_escalation_state

            clear_fingerprint_escalation_state(task)
        except Exception:
            pass
        try:
            from backend.services.backup_model import clear_backup_remaining, restore_primary_model
            from backend.agents.registry import agent_cr, agent_dev, agent_po, agent_qa

            clear_backup_remaining(task)
            try:
                from backend.services.cloud_dev_provider import clear_cloud_remaining

                clear_cloud_remaining(task)
            except Exception:
                pass
            # Restore all agents to primary after a successful lane move
            restore_primary_model(agent_po, "po")
            restore_primary_model(agent_dev, "dev")
            restore_primary_model(agent_cr, "cr")
            restore_primary_model(agent_qa, "qa")
        except Exception:
            pass
        return

    task["stuckLoops"] = int(task.get("stuckLoops", 0)) + 1

    # Circuit breaker: identical patch fails / consecutive unhealthy exits escalate early.
    try:
        from backend.services.sprint_speed_gates import circuit_breaker_should_trip

        trip, trip_reason = circuit_breaker_should_trip(task)
        if trip:
            ws_cb = get_workflow_settings()
            max_stuck_cb = int(ws_cb.get("maxStuckSteps", 3))
            task["stuckLoops"] = max_stuck_cb
            record_task_decision(
                task_id,
                "System",
                "stuck_circuit_breaker",
                trip_reason,
                "Stopping endless In Progress retries — split or escalate.",
            )
            add_system_log("System", "warning", f"{task_id}: {trip_reason}")
            if lane_after == "Needs PO":
                from backend.services.sprint_speed_gates import latch_needs_po_auto_skip

                latch_needs_po_auto_skip(task, reason=trip_reason)
                add_system_log(
                    "System",
                    "warning",
                    f"{task_id}: skipping further auto PO on this card until it is unblocked",
                )
    except Exception:
        pass

    # Arm backup model for the next step(s) on reasoning/tool-use stuck (not lint walls).
    key = agent_key
    if not key:
        role = str(state.ACTIVE_SPRINT_AGENT or "")
        key = {
            "Developer": "dev",
            "Product Owner": "po",
            "Code Reviewer": "cr",
            "QA Tester": "qa",
        }.get(role)
    if key and task["stuckLoops"] >= 1:
        try:
            from backend.services.backup_model import (
                arm_backup_for_agent,
                latest_loop_stop_exit_reason,
                should_force_arm_from_exit_reason,
            )

            loop_reason = latest_loop_stop_exit_reason()
            force = should_force_arm_from_exit_reason(loop_reason)
            arm_backup_for_agent(
                key,
                task,
                reason=(
                    f"{loop_reason}; no lane move (stuckLoops={task['stuckLoops']})"
                    if loop_reason
                    else f"no lane move (stuckLoops={task['stuckLoops']})"
                ),
                force=force,
            )
        except Exception:
            pass

    if task["stuckLoops"] >= 1:
        try:
            from backend.agents.tool_fingerprints import should_escalate_repeat_tool_overlap

            if should_escalate_repeat_tool_overlap(task):
                ws = get_workflow_settings()
                max_stuck = int(ws.get("maxStuckSteps", 3))
                task["stuckLoops"] = max_stuck
                record_task_decision(
                    task_id,
                    "System",
                    "stuck_loop",
                    "Repeat tool overlap — escalating early",
                    "Consecutive steps used the same tool calls; change approach or edit files.",
                )
                add_system_log(
                    "System",
                    "warning",
                    f"{task_id}: repeat tool fingerprint overlap — treating as max stuck",
                )
        except Exception:
            pass

    ws = get_workflow_settings()
    max_stuck = int(ws.get("maxStuckSteps", 3))
    if task["stuckLoops"] < max_stuck:
        return

    # Do not yank cards already escalated / finished.
    if lane_after in ("Needs PO", "Needs User", "Done", "Features"):
        return

    # Cloud model recovery: one attempt before auto-split / Needs PO.
    if key == "dev" and not task.get("cloudRecoveryAttempted"):
        try:
            from backend.services.cloud_dev_provider import arm_cloud_for_agent, cloud_dev_enabled

            if cloud_dev_enabled(ws):
                if arm_cloud_for_agent(
                    "dev",
                    task,
                    reason=f"stuckLoops={task['stuckLoops']} before split",
                ):
                    task["cloudRecoveryAttempted"] = True
                    task["stuckLoops"] = max(0, max_stuck - 1)
                    task["forcePatchNextDevStep"] = True
                    add_system_log(
                        "System",
                        "info",
                        f"{task_id}: cloud Dev recovery armed — one more implementer step before split",
                    )
                    return
        except Exception:
            pass

    # Ladder: backup already armed above → try one auto-split before Needs PO.
    # Unlatched lint/tool walls skip auto-split (fanout + Forced Patch get a shot).
    # Latched cards auto-split even for lint. Skip first explore-budget for Forced Patch.
    from backend.services.sprint_speed_gates import (
        last_step_exit_reason,
        stuck_is_explore_without_write,
    )

    explore_no_write = stuck_is_explore_without_write(task)
    latched = bool(task.get("phaseCycleCapReached"))
    if _should_attempt_stuck_auto_split(task, ws):
        if _run_stuck_auto_split(task_id, task, max_stuck):
            return
        if latched:
            task["poAutoSkip"] = True
            add_system_log(
                "System",
                "warning",
                f"{task_id}: latched auto-split did not supersede parent — "
                "staying In Progress (not Needs User)",
            )
            return

    max_po = int(ws.get("maxPoRoundTrips", 3))
    msg = build_stuck_escalation_message(task, lane_after, max_stuck)
    if latched and is_lint_wall_card(task):
        record_task_decision(
            task_id,
            "System",
            "stuck_loop",
            "Lint wall on a latched card — staying In Progress for Developer",
            msg,
        )
        _keep_lint_card_for_developer(task)
        return
    if latched:
        park_msg = (
            "Phase cycle cap reached. Split the card or reset the Developer visit latch. "
            "Auto Sprint will not run Product Owner or Developer on this card."
        )
        record_task_decision(
            task_id,
            "System",
            "stuck_loop",
            "Phase cycle cap — parking to Needs User (skip PO/Dev)",
            park_msg,
        )
        add_system_log(
            "System",
            "warning",
            f"{task_id}: phase cycle cap — parking to Needs User instead of Needs PO",
        )
        _try_move_to_needs_user(task_id, task, park_msg, kind="phase_cycle_cap")
        return
    if explore_no_write and not latched and not task.get("forcePatchAttempted"):
        task["forcePatchNextDevStep"] = True
        record_task_decision(
            task_id,
            "System",
            "stuck_loop",
            "Explore budget exhausted without a write — staying In Progress for a forced Patch turn",
            msg,
        )
        add_system_log(
            "System",
            "warning",
            f"{task_id}: explore budget exhausted — not bouncing to Needs PO; next Dev step is Patch",
        )
        return

    exit_r = last_step_exit_reason(task)
    if exit_r in ("llm_call_failed", "empty_generation_timeout"):
        if int(task.get("consecutiveEmptyGen") or 0) >= 2:
            park_msg = (
                "Ollama repeatedly timed out without generating a response. "
                "Check that Ollama is running, reduce num_ctx, or lower ollamaRequestTimeoutSec."
            )
            record_task_decision(
                task_id,
                "System",
                "stuck_loop",
                "Repeated empty-generation timeout — parking to Needs User",
                park_msg,
            )
            add_system_log(
                "System",
                "warning",
                f"{task_id}: repeated {exit_r} — parking to Needs User",
            )
            _try_move_to_needs_user(task_id, task, park_msg, kind="stuck")
            return
        from backend.services.po_clarification import task_has_ready_spec

        if task_has_ready_spec(task):
            task["forcePatchNextDevStep"] = True
            record_task_decision(
                task_id,
                "System",
                "stuck_loop",
                "LLM call failed with a spec already present — staying In Progress (not Needs PO)",
                msg,
            )
            add_system_log(
                "System",
                "warning",
                f"{task_id}: {exit_r} — not bouncing to Needs PO; next Dev step is Patch",
            )
            return
    if exit_r == "identical_write_loop":
        park_msg = (
            "Identical write loop — the same file was rewritten without new progress. "
            "Split the card or edit the file, then return to In Progress."
        )
        if is_lint_wall_card(task):
            record_task_decision(
                task_id,
                "System",
                "stuck_loop",
                "Identical write loop on lint wall card — file blocker or stay In Progress",
                park_msg,
            )
            _handle_lint_stuck(task_id, task, park_msg)
            return
        record_task_decision(
            task_id,
            "System",
            "stuck_loop",
            "Identical write loop — parking instead of Needs PO",
            park_msg,
        )
        _try_move_to_needs_user(task_id, task, park_msg, kind="phase_cycle_cap")
        return
    if exit_r in ("max_iterations_after_writes", "completed_with_writes") and _task_has_write_files(
        task
    ):
        if is_lint_wall_card(task):
            task["forcePatchNextDevStep"] = True
            record_task_decision(
                task_id,
                "System",
                "stuck_loop",
                "Wrote files then hit the iteration cap — staying In Progress (not Needs PO)",
                msg,
            )
            add_system_log(
                "System",
                "warning",
                f"{task_id}: write-stop — not bouncing to Needs PO; next Dev step is Patch",
            )
        return
    if int(task.get("poRoundTrips", 0)) >= max_po:
        # Lint walls stay In Progress so Dev can retry (including after a visit cap).
        if is_lint_wall_card(task):
            record_task_decision(
                task_id,
                "System",
                "stuck_loop",
                f"Lint wall after {max_stuck} steps — not escalating to Needs User",
                msg,
            )
            add_system_log(
                "System",
                "warning",
                f"{task_id}: stuck on lint wall — fix code or run diagnosis; not moving to Needs User",
            )
            _keep_lint_card_for_developer(task)
        else:
            _try_move_to_needs_user(task_id, task, msg)
    else:
        stuck_msg = (
            f"Agents made no progress after {max_stuck} steps in '{lane_after}' — "
            "escalating to PO for clarification."
        )
        if stuck_is_tool_or_lint(task):
            stuck_msg = (
                f"No progress after {max_stuck} steps — lint/tool issues remain. "
                "PO will help refine approach."
            )
        increment_po_round_trips(task_id)
        move_board_stage(task_id, "Needs PO")
        publish_activity(
            task_id,
            "stuck_loop",
            stuck_msg,
            role="system",
            agent="System",
            lane="Needs PO",
        )
        add_system_log("System", "warning", f"{task_id}: stuck loop → Needs PO")
        try:
            from backend.services.phone_notify import notify_if_enabled

            title = task.get("title") or task_id
            notify_if_enabled(
                "stuck_escalation",
                "Stuck escalation → Needs PO",
                f"{title}\n{stuck_msg[:400]}",
                task_id=task_id,
            )
        except Exception:
            pass


def _escalate_po_limit(task: Dict[str, Any]) -> bool:
    """Move to Needs User when PO round-trip limit exceeded."""
    normalize_task(task)
    max_trips = int(get_workflow_settings().get("maxPoRoundTrips", 3))
    if task.get("poRoundTrips", 0) < max_trips:
        return False
    msg = (
        f"PO and Dev could not agree after {max_trips} rounds — "
        "please clarify requirements."
    )
    if not _try_move_to_needs_user(task["id"], task, msg, kind="po_limit"):
        return False
    return True


def _dev_needs_user(result: str) -> bool:
    return dev_explicit_needs_user(result)


_NO_AUTO_EXTEND_STOPS = frozenset(
    {"duplicate_tool", "step_timeout", "tool_failure_stop"}
)
_WRITE_TOOLS = frozenset({"write_file", "apply_patch"})


def _result_is_max_iterations(result: Optional[str]) -> bool:
    if not result:
        return False
    text = str(result)
    if text.startswith("Max tool iterations"):
        return True
    lower = text.lower()
    return (
        "max tool iterations" in lower
        or "max_iterations" in lower
        or "files already written this step" in lower
    )


def _step_trace_has_verify() -> bool:
    try:
        from backend.services.duplicate_tool_policy import tools_log_has_verify
        from backend.services.step_diagnostics import get_active_trace

        trace = get_active_trace()
        return bool(trace and tools_log_has_verify(getattr(trace, "tools_log", None) or []))
    except Exception:
        return False


def _maybe_auto_extend_dev_step(
    task_id: str,
    task: Dict[str, Any],
    result: str,
) -> str:
    """
    One auto-extend on max_iterations when progress is evident (writes or tools),
    skipping loop-stop exits. Sets task.autoExtendUsed for the stuck cycle.
    Also extends once after a write-stop when verify has not run yet (even if
    autoExtendOnMaxIter is off).
    """
    ws = get_workflow_settings()
    normalize_task(task)
    if task.get("autoExtendUsed"):
        return result
    if not _result_is_max_iterations(result):
        return result

    progress = (
        state.LAST_STEP_PROGRESS
        if isinstance(state.LAST_STEP_PROGRESS, dict)
        else None
    ) or (task.get("lastStepProgress") if isinstance(task.get("lastStepProgress"), dict) else {})
    if state.LAST_STEP_OUTCOME and isinstance(state.LAST_STEP_OUTCOME.get("stepProgress"), dict):
        progress = state.LAST_STEP_OUTCOME["stepProgress"] or progress

    # Prefer this-step progress / result text — ignore stale LAST_STEP_OUTCOME stops.
    stop = ""
    if isinstance(progress, dict):
        stop = str(progress.get("stopReason") or progress.get("exitReason") or "").lower()
    result_l = str(result).lower()
    if any(
        marker in result_l
        for marker in ("duplicate tool", "timed out", "tool failure", "same failing", "identical write loop")
    ):
        return result
    if stop in _NO_AUTO_EXTEND_STOPS:
        return result

    tools_raw = (progress or {}).get("toolsUsed") or []
    tools = {str(t) for t in tools_raw} if isinstance(tools_raw, (list, set, tuple)) else set()
    has_writes = bool(tools & _WRITE_TOOLS) or _task_has_write_files(task)
    has_tools = bool(tools)
    verify_known = _step_trace_has_verify()
    verify_only = has_writes and not verify_known
    if verify_known:
        return result
    allow_general = bool(ws.get("autoExtendOnMaxIter", True)) and (has_writes or has_tools)
    if not (verify_only or allow_general):
        return result
    if not (has_writes or has_tools):
        return result

    extra = 2 if verify_only and not allow_general else max(1, min(int(ws.get("autoExtendExtraIterations") or 4), 16))
    extra = max(1, min(int(extra), 16))
    add_system_log(
        "Developer",
        "info",
        f"Auto-extend +{extra} iterations ({'verify after write' if verify_only else 'progress detected'})",
    )
    task["autoExtendUsed"] = True
    try:
        from backend.services.prompt_retry import extend_agent_step

        ollama_url = str(getattr(state, "OLLAMA_URL", "") or "").strip()
        if not ollama_url:
            from backend.services.llm_provider import chat_config

            ollama_url = str(chat_config().get("baseUrl") or "http://localhost:11434")
        ext = extend_agent_step(
            task_id,
            "dev",
            ollama_url,
            action="extend",
            extra_iterations=extra,
            brief=state.PROJECT_BRIEF or "",
        )
        out = ext.get("output")
        if isinstance(out, str) and out:
            return out
    except Exception as exc:
        add_system_log(
            "Developer",
            "warning",
            f"Auto-extend failed ({type(exc).__name__}) — continuing with original result",
        )
    return result


def _mark_sprint_step_start() -> str:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    state.SPRINT_STEP_STARTED_AT = ts
    try:
        import time as _time

        state.SPRINT_STEP_STARTED_MONO = _time.monotonic()
    except Exception:
        state.SPRINT_STEP_STARTED_MONO = None
    state.STEP_FILE_READS.clear()
    state.STEP_PATH_TOOL_FAILURES.clear()
    state.STEP_PATCH_FAILURES.clear()
    state.DEV_STEP_READ_ONLY_NO_EDITS = False
    from backend.services.tool_cache import clear_tool_cache

    clear_tool_cache()
    return ts


# Actions that prove the Developer wrote/edited code (not reads or context preload).
_WRITE_FILE_ACTIONS = frozenset({"written", "write", "edited"})


def _task_has_work_files(task: Dict[str, Any]) -> bool:
    """True when the task has agent-touched files beyond sprint context preload."""
    for f in task.get("files") or []:
        if isinstance(f, str):
            return True
        if isinstance(f, dict) and f.get("path"):
            action = str(f.get("action") or "touched")
            if action not in ("context", "touched"):
                return True
    return False


def _task_has_write_files(task: Dict[str, Any]) -> bool:
    """True when the task has at least one successful write/edit file action."""
    for f in task.get("files") or []:
        if not isinstance(f, dict) or not f.get("path"):
            continue
        action = str(f.get("action") or "").lower()
        if action in _WRITE_FILE_ACTIONS:
            return True
    return False


FORCE_PATCH_INSTRUCTION = (
    "FORCED PATCH: Call write_file or apply_patch this turn — do not only explore. "
    "If the workspace has no source files, scaffold first (flutter create . / write_file stubs) "
    "then implement the card."
)


def _lint_fix_hint(task: Dict[str, Any]) -> str:
    """Concrete first diagnostic for Lint: / lintSourceFile cards."""
    title = str(task.get("title") or "")
    is_lint = title.startswith("Lint: ") or bool(task.get("lintSourceFile"))
    if not is_lint and not stuck_is_tool_or_lint(task):
        return ""
    diags = task.get("lastCommandDiagnostics") or []
    if not isinstance(diags, list) or not diags:
        return ""
    first = diags[0] if isinstance(diags[0], dict) else {}
    file_path = str(first.get("file") or task.get("lintSourceFile") or "").strip()
    line = int(first.get("line") or 0)
    message = str(first.get("message") or "").strip()
    if not file_path:
        return ""
    loc = f"{file_path}:{line}" if line else file_path
    detail = f" — {message}" if message else ""
    return (
        f"Fix this diagnostic first ({loc}{detail}). "
        f"Call apply_patch or write_file on {file_path}; do not reply with text only."
    )


def _patch_recovery_hint(task: Dict[str, Any]) -> str:
    """Concrete write_file fallback when apply_patch keeps failing."""
    from backend.services.needs_user_guard import _last_failed_tool

    patch_fails = int(task.get("identicalPatchFailCount") or 0)
    failed_tool = _last_failed_tool(task)
    progress = task.get("lastStepProgress") if isinstance(task.get("lastStepProgress"), dict) else {}
    last_summary = str((progress or {}).get("lastToolSummary") or "").strip()
    tool_blob = failed_tool or last_summary
    tool_lower = tool_blob.lower()
    if patch_fails >= 2 or (
        tool_blob and "apply_patch" in tool_lower and "old_text" in tool_lower
    ):
        if tool_blob:
            return (
                f"Last patch error ({tool_blob}). "
                "Call read_file for the full file, then write_file with the complete corrected content."
            )
        return (
            "Identical apply_patch failed twice — call read_file for the full file, "
            "then write_file with the complete corrected content."
        )
    if tool_blob and "apply_patch" in tool_lower:
        return (
            f"Last patch error ({tool_blob}). "
            "Re-read with read_file, then apply_patch or write_file the full file."
        )
    return ""


def _should_slim_dev_prompt(task: Optional[Dict[str, Any]]) -> bool:
    """Tight prompt for forced-patch, lint-wall, and stuck Developer steps."""
    if not isinstance(task, dict):
        return False
    try:
        from backend.services.needs_user_guard import is_lint_wall_card
        from backend.services.sprint_speed_gates import (
            last_step_exit_reason,
            should_force_patch_next_dev_step,
        )

        if should_force_patch_next_dev_step(task) or is_lint_wall_card(task):
            return True
        if task.get("forcePatchNextDevStep"):
            return True
        if int(task.get("consecutiveBadExits") or 0) >= 2:
            return True
        prior = last_step_exit_reason(task)
        if prior in {"text_rejection_loop", "explore_budget_exhausted", "llm_call_failed"}:
            return True
        if _count_task_tool_failures(task) >= 3:
            return True
    except Exception:
        return bool(task.get("forcePatchNextDevStep"))
    return False


def _force_patch_dev_instruction(task: Optional[Dict[str, Any]], agent_role: str) -> str:
    if agent_role != "Developer" or not isinstance(task, dict):
        return ""
    slim = _should_slim_dev_prompt(task)
    identical_patch = int(task.get("identicalPatchFailCount") or 0) >= 2
    recovery = _patch_recovery_hint(task) if (not slim or identical_patch) else ""
    try:
        from backend.services.sprint_speed_gates import should_force_patch_next_dev_step

        if should_force_patch_next_dev_step(task):
            hint = _lint_fix_hint(task)
            parts = [FORCE_PATCH_INSTRUCTION]
            if recovery:
                parts.append(recovery)
            elif hint:
                parts.append(hint)
            return "\n".join(parts)
    except Exception:
        if task.get("forcePatchNextDevStep"):
            hint = _lint_fix_hint(task)
            parts = [FORCE_PATCH_INSTRUCTION]
            if recovery:
                parts.append(recovery)
            elif hint:
                parts.append(hint)
            return "\n".join(parts)
    return ""


def _inject_sprint_context(
    active_task: Dict[str, Any],
    brief: str,
    agent_role: str,
    instructions: str,
) -> str:
    """Build sprint prompt with pre-loaded file contents (no Tools log row per step)."""
    from backend.services.prompt_budget import (
        initial_ollama_num_ctx,
        sprint_preload_budgets,
    )
    from backend.services.prompt_profile import (
        is_local_slm_profile,
        local_slm_sections_for_role,
        local_slm_sprint_preload_enabled,
    )
    from backend.storage.code_index import build_semantic_sprint_context

    task_id = active_task["id"]
    normalize_task(active_task)
    if agent_role == "Developer":
        try:
            from backend.services.file_blocker import infer_lint_source_file

            inferred = infer_lint_source_file(active_task)
            if inferred and not active_task.get("lintSourceFile"):
                active_task["lintSourceFile"] = inferred
        except Exception:
            pass
        try:
            from backend.services.task_spec_markdown import ensure_task_spec_for_work

            ensure_task_spec_for_work(str(task_id))
        except Exception:
            pass
    from backend.services.prompt_sections import FocusContext, compose_prompt

    ws = get_workflow_settings()
    from backend.services.workflow_settings import get_execution_profile

    implementer = get_execution_profile(ws) == "implementer"
    local_slm = is_local_slm_profile(ws)
    preload = local_slm_sprint_preload_enabled(ws)
    num_ctx = initial_ollama_num_ctx(agent_role)
    slim_dev_prompt = agent_role == "Developer" and _should_slim_dev_prompt(active_task)
    semantic_block, sem_paths = "", []
    graph_block = ""
    file_block, file_paths = "", []
    context_block = ""
    graph_used = False
    if preload and slim_dev_prompt:
        budgets = sprint_preload_budgets(num_ctx, local_slm=local_slm, role=agent_role)
        file_block, file_paths = build_sprint_file_context(
            active_task,
            max_chars=min(int(budgets.get("total") or 6000), 6000),
        )
        context_block = file_block or ""
    elif preload:
        budgets = sprint_preload_budgets(num_ctx, local_slm=local_slm, role=agent_role)
        top_k_override = None
        if implementer:
            top_k_override = min(max(1, int(ws.get("semanticSprintTopK") or 3)), 3)
        elif local_slm:
            top_k_override = min(max(1, int(ws.get("semanticSprintTopK") or 5)), 2)

        def _build_semantic():
            return build_semantic_sprint_context(
                active_task,
                max_chars=budgets["semantic"],
                top_k_override=top_k_override,
            )

        def _build_graph():
            if implementer and not ws.get("enableSemanticSprintContext", True):
                return ""
            try:
                from backend.services.graphify_service import (
                    build_graphify_sprint_context,
                    graphify_status,
                )

                if graphify_status().get("available") or graphify_status().get("reportExists"):
                    return build_graphify_sprint_context(
                        active_task,
                        max_chars=budgets["graph"] if not implementer else min(budgets["graph"], 2000),
                    )
            except Exception:
                return ""
            return ""

        # Parallelize independent context builders (semantic + graph), then file budget.
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=2) as pool:
            fut_sem = pool.submit(_build_semantic)
            fut_graph = pool.submit(_build_graph)
            try:
                semantic_block, sem_paths = fut_sem.result()
            except Exception:
                semantic_block, sem_paths = "", []
            try:
                graph_block = fut_graph.result() or ""
            except Exception:
                graph_block = ""
        graph_used = bool(graph_block)
        total_budget = budgets["total"]
        file_budget = (
            max(1000, total_budget - len(semantic_block) - len(graph_block))
            if (semantic_block or graph_block)
            else total_budget
        )
        file_block, file_paths = build_sprint_file_context(active_task, max_chars=file_budget)
        context_block = "".join(part for part in (semantic_block, graph_block, file_block) if part)
    paths = list(dict.fromkeys([*sem_paths, *file_paths]))
    if paths:
        if semantic_block and file_block:
            add_system_log(
                agent_role,
                "info",
                f"Pre-loaded semantic + {len(file_paths)} file(s) for {task_id}",
            )
        elif semantic_block:
            add_system_log(
                agent_role,
                "info",
                f"Pre-loaded {len(sem_paths)} semantic chunk(s) for {task_id}",
            )
        else:
            add_system_log(
                agent_role,
                "info",
                f"Pre-loaded {len(paths)} file(s) for {task_id}: {', '.join(paths[:8])}"
                + ("…" if len(paths) > 8 else ""),
            )
        task = find_task_by_id(task_id)
        if task:
            normalize_task(task)
            stronger = {"written", "tested", "read"}
            existing_actions = {
                f.get("path"): f.get("action")
                for f in task.get("files", [])
                if isinstance(f, dict) and f.get("path")
            }
            for path in paths:
                if existing_actions.get(path) in stronger:
                    continue
                record_task_file(task_id, path, "context", persist=True)

    codebase_pack = ""
    pack_mode = "off"
    if agent_role == "Developer" and preload and not slim_dev_prompt:
        pack_mode = str(get_workflow_settings().get("contextPacker") or "off").strip().lower()
        if pack_mode not in ("", "off", "none", "false"):
            try:
                from backend.services.context_packer import run_context_pack
                from backend.services.focus_slice import default_pack_paths

                acs = active_task.get("acceptanceCriteria") or []
                idx = int(active_task.get("focusAcIndex") or 0)
                hint = str(active_task.get("title") or "")
                if acs and idx < len(acs):
                    hint = f"{hint} {acs[idx]}"
                codebase_pack = run_context_pack(default_pack_paths(active_task), query_hint=hint)
                if codebase_pack:
                    add_system_log(
                        "Developer",
                        "info",
                        f"Codebase packer ({pack_mode}): {len(codebase_pack)} chars for {task_id}",
                    )
                else:
                    add_system_log(
                        "Developer",
                        "warning",
                        f"Codebase packer ({pack_mode}) returned no output for {task_id} — check CLI on PATH (see README)",
                    )
            except Exception:
                codebase_pack = ""

    from backend.services.sprint_context_sources import (
        build_context_sources_snapshot,
        set_last_sprint_context_sources,
    )

    set_last_sprint_context_sources(
        build_context_sources_snapshot(
            task_id=task_id,
            agent_role=agent_role,
            local_slm=local_slm,
            semantic_paths=sem_paths,
            file_paths=file_paths,
            graph_used=graph_used,
            pack_mode=pack_mode,
            codebase_pack_chars=len(codebase_pack or ""),
        )
    )

    use_focus_compose = False
    if local_slm:
        focus = FocusContext(agent_role=agent_role, focus_mode="whole", include_full_spec=False)
        section_ids = list(local_slm_sections_for_role(agent_role, active_task))
        if codebase_pack and codebase_pack.strip() and "codebase_pack" not in section_ids:
            section_ids.append("codebase_pack")
        base = compose_prompt(
            active_task,
            brief,
            section_ids,
            focus,
            agent_role=agent_role,
            codebase_pack=codebase_pack,
        )
        state.SPRINT_PROMPT_ROTATION_ENABLED = False
        state.SPRINT_PROMPT_ROTATION_BLOCKS = []
        state.SPRINT_PROMPT_ROTATION_NAMES = []
    elif slim_dev_prompt and agent_role == "Developer":
        from backend.services.prompt_sections import FocusContext, compose_prompt

        focus = FocusContext(
            agent_role=agent_role,
            focus_mode="whole",
            include_full_spec=False,
        )
        slim_sections = [
            "card_core",
            "ac_focus",
            "last_outcome",
            "lane_instructions",
        ]
        base = compose_prompt(
            active_task,
            brief,
            slim_sections,
            focus,
            agent_role=agent_role,
        )
        add_system_log(
            agent_role,
            "info",
            f"Slim recovery prompt for {task_id} ({len(base)} chars)",
        )
    else:
        base = build_task_prompt(active_task, brief, agent_role=agent_role)
    if not local_slm and agent_role == "Developer" and not slim_dev_prompt:
        from backend.services.focus_slice import (
            dev_micro_steps_enabled,
            focus_context_from_task,
            prepare_in_step_rotation_blocks,
            sections_for_focus,
        )
        from backend.services.prompt_sections import compose_prompt

        if dev_micro_steps_enabled(active_task):
            focus = focus_context_from_task(active_task, agent_role)
            section_ids = sections_for_focus(focus, phase="micro_step")
            base = compose_prompt(
                active_task,
                brief,
                section_ids,
                focus,
                agent_role=agent_role,
                codebase_pack=codebase_pack,
            )
            use_focus_compose = True
            label = ""
            try:
                from backend.services.focus_slice import focus_log_label

                label = focus_log_label(active_task)
            except Exception:
                pass
            if label:
                add_system_log("Developer", "info", label)
            rot_blocks, rot_names = prepare_in_step_rotation_blocks(
                active_task,
                brief,
                agent_role=agent_role,
                codebase_pack=codebase_pack,
            )
            state.SPRINT_PROMPT_ROTATION_ENABLED = bool(rot_blocks)
            state.SPRINT_PROMPT_ROTATION_BLOCKS = rot_blocks
            state.SPRINT_PROMPT_ROTATION_NAMES = rot_names
        else:
            state.SPRINT_PROMPT_ROTATION_ENABLED = False
            state.SPRINT_PROMPT_ROTATION_BLOCKS = []
            state.SPRINT_PROMPT_ROTATION_NAMES = []
        if (
            pack_mode not in ("", "off", "none", "false")
            and codebase_pack
            and not dev_micro_steps_enabled(active_task)
        ):
            from backend.services.prompt_sections import FocusContext, build_section, _limits_for_role

            focus = focus_context_from_task(active_task, agent_role)
            limits = _limits_for_role(agent_role)
            pack_block = build_section(
                "codebase_pack",
                active_task,
                brief,
                focus=focus,
                limits=limits,
                codebase_pack=codebase_pack,
            )
            if pack_block:
                base = base + "\n\n" + pack_block.strip()

    parts = [base]
    if context_block:
        from backend.services.context_compress import maybe_compress_sprint_context_block

        context_block = maybe_compress_sprint_context_block(context_block, agent_role=agent_role)
        parts.append(context_block)
        parts.append(CONTEXT_INJECT_NOTE)
    structure_audit = ""
    if agent_role == "Developer" and not local_slm and not slim_dev_prompt:
        try:
            from backend.services.workspace_structure_audit import (
                audit_workspace_structure,
                format_structure_audit,
            )

            structure_audit = format_structure_audit(audit_workspace_structure())
        except Exception:
            structure_audit = ""
    if structure_audit:
        parts.append(structure_audit)
    if agent_role == "Developer":
        scaffolded = [
            str(p).replace("\\", "/")
            for p in (active_task.get("scaffoldedFiles") or [])
            if str(p).strip()
        ]
        if scaffolded:
            shown = ", ".join(scaffolded[:8])
            extra = f" (+{len(scaffolded) - 8} more)" if len(scaffolded) > 8 else ""
            parts.append(
                "=== SCAFFOLDED FILES (replace with full implementation) ===\n"
                f"Auto-scaffold stubs on this card: {shown}{extra}\n"
                "Replace each stub with complete working code before advancing to QA."
            )
    if agent_role == "Developer" and not local_slm and not slim_dev_prompt:
        try:
            from backend.services.workspace_structure_audit import greenfield_wander_nudge

            wander = greenfield_wander_nudge(brief=brief, task=active_task)
            if wander:
                parts.append(wander)
        except Exception:
            pass
    force_patch_note = _force_patch_dev_instruction(active_task, agent_role)
    if force_patch_note:
        parts.append(force_patch_note)

    if use_focus_compose and state.SPRINT_PROMPT_ROTATION_ENABLED:
        state.SPRINT_PROMPT_FIXED_PREFIX = "\n\n".join(parts)
        state.SPRINT_PROMPT_FIXED_SUFFIX = instructions
        rot0 = state.SPRINT_PROMPT_ROTATION_BLOCKS[0] if state.SPRINT_PROMPT_ROTATION_BLOCKS else ""
        return "\n\n".join([state.SPRINT_PROMPT_FIXED_PREFIX, rot0, state.SPRINT_PROMPT_FIXED_SUFFIX])
    state.SPRINT_PROMPT_FIXED_PREFIX = ""
    state.SPRINT_PROMPT_FIXED_SUFFIX = ""
    parts.append(instructions)
    return "\n\n".join(parts)


def _qa_result_indicates_failure(result: str) -> bool:
    lower = result.lower()
    return any(m in lower for m in ("fail", "failed", "rejected", "does not pass", "not pass"))


def _qa_failed(result: str) -> bool:
    """Legacy helper — prefer _qa_step_passed."""
    return _qa_result_indicates_failure(result)


def _transcript_entries_since(task: Dict[str, Any], step_started: str) -> List[Dict[str, Any]]:
    entries: List[Dict[str, Any]] = []
    for entry in task.get("transcript") or []:
        if not isinstance(entry, dict):
            continue
        ts = str(entry.get("timestamp") or "")
        if step_started and ts and ts < step_started:
            continue
        entries.append(entry)
    return entries


def _qa_has_test_evidence(
    task: Dict[str, Any],
    playbook: Dict[str, Any],
    step_started: str,
) -> bool:
    if playbook.get("run") and playbook.get("passed"):
        return True
    for entry in _transcript_entries_since(task, step_started):
        if entry.get("toolName") in ("run_test", "run_command"):
            if entry.get("toolSuccess") is not False:
                return True
    return False


def _qa_step_passed(
    task: Dict[str, Any],
    result: str,
    playbook: Dict[str, Any],
    step_started: str,
) -> tuple[bool, str]:
    evidence = task.get("qaEvidence") or {}
    if evidence.get("userOverride"):
        return True, ""
    if playbook.get("run") and not playbook.get("passed"):
        return False, "Automated test playbook failed"
    if _qa_result_indicates_failure(result):
        return False, "QA agent reported failure"
    if _qa_has_test_evidence(task, playbook, step_started):
        return True, ""
    if not playbook.get("run"):
        return False, "No test playbook available and no run_test/run_command evidence"
    return False, "No successful test evidence in this QA step"


def qa_gate_blocks_done(task: Dict[str, Any]) -> tuple[bool, str]:
    """Return (blocked, reason) when agent tries update_board → Done from QA."""
    normalize_task(task)
    ws = get_workflow_settings()
    try:
        from backend.services.implementer_profile import implementer_gate_relaxation_active

        if implementer_gate_relaxation_active(ws):
            from backend.services.card_ledger import oracle_blocks_done

            blocked, reason = oracle_blocks_done(task)
            if blocked:
                return True, reason
            return False, ""
    except Exception:
        pass
    evidence = task.get("qaEvidence") or {}
    if evidence.get("userOverride"):
        return False, ""
    step_started = state.SPRINT_STEP_STARTED_AT or ""
    if evidence.get("playbookRun") and not evidence.get("passed"):
        return True, "Automated test playbook failed — cannot move to Done."
    if not _qa_has_test_evidence(task, evidence, step_started):
        return True, "No test evidence — run_test or run_command must succeed before Done."
    from backend.services.file_completeness import completeness_gate_blocks

    blocked_complete, complete_reason = completeness_gate_blocks(task)
    if blocked_complete:
        return True, complete_reason
    if get_workflow_settings().get("requireCleanLint"):
        diagnostics = task.get("lastCommandDiagnostics") or []
        if diagnostics:
            return True, f"Unresolved lint ({len(diagnostics)} issues) — fix before Done."
    ws = get_workflow_settings()
    if ws.get("requireAcChecklistForDone", True):
        acs = [str(c).strip() for c in (task.get("acceptanceCriteria") or []) if str(c).strip()]
        if acs:
            checks = task.get("acChecklist")
            if not isinstance(checks, list):
                checks = []
            # Pad/truncate alignment
            while len(checks) < len(acs):
                checks.append(False)
            checks = checks[: len(acs)]
            task["acChecklist"] = [bool(x) for x in checks]
            unchecked = sum(1 for c in task["acChecklist"] if not c)
            if unchecked:
                return (
                    True,
                    f"Acceptance criteria unchecked ({unchecked}/{len(acs)}) — "
                    "check all ACs in the card (or set qaEvidence.userOverride).",
                )
    from backend.services.card_ledger import oracle_blocks_done

    blocked, reason = oracle_blocks_done(task)
    if blocked:
        return True, reason
    return False, ""


def _playbook_item_failed(outcome: str) -> bool:
    return outcome in ("execution_failed", "test_failed")


def _playbook_outcome_label(outcome: str) -> str:
    if outcome == "ok":
        return "PASS"
    if outcome == "lint_findings":
        return "FINDINGS"
    return "FAIL"


def _run_qa_test_playbook(task_id: str) -> Dict[str, Any]:
    """Run project test commands before the QA agent evaluates."""
    from backend.services.command_result import format_command_result_for_agent, run_workspace_command

    commands = derive_project_test_commands()
    results: List[Dict[str, Any]] = []
    all_passed = True

    for cmd in commands:
        cmd_result = run_workspace_command(cmd)
        output = format_command_result_for_agent(cmd_result)
        outcome = cmd_result.outcome
        tool_success = outcome != "execution_failed"
        if _playbook_item_failed(outcome):
            all_passed = False
        results.append(
            {
                "command": cmd,
                "success": outcome == "ok",
                "outcome": outcome,
                "output": output[:1500],
                "diagnosticsCount": len(cmd_result.diagnostics),
            }
        )
        log_synthetic_tool_event(
            task_id,
            "QA Tester",
            "run_command",
            tool_args={"command": cmd},
            tool_output=output,
            success=tool_success,
            source="orchestrator",
        )

    return {
        "run": bool(commands),
        "commands": commands,
        "results": results,
        "passed": all_passed if commands else False,
    }


def _format_playbook_block(playbook: Dict[str, Any]) -> str:
    if not playbook.get("run"):
        return "\n=== AUTOMATED TEST RESULTS (orchestrator) ===\n(no project test commands detected)\n"
    lines = ["\n=== AUTOMATED TEST RESULTS (orchestrator) ==="]
    for item in playbook.get("results") or []:
        outcome = item.get("outcome")
        if outcome:
            status = _playbook_outcome_label(str(outcome))
        else:
            status = "PASS" if item.get("success") else "FAIL"
        lines.append(f"- [{status}] {item.get('command', '?')}")
        excerpt = str(item.get("output") or "")[:400].replace("\n", " ")
        if excerpt:
            lines.append(f"  {excerpt}")
    lines.append(f"Overall playbook: {'PASSED' if playbook.get('passed') else 'FAILED'}\n")
    return "\n".join(lines)


def inject_tool_evidence_for_task(
    task_id: str,
    tool_name: str,
    tool_args: Dict[str, Any],
    tool_output: str,
    *,
    note: str = "",
) -> Dict[str, Any]:
    """Inject user-provided tool output onto a task and unblock QA when appropriate."""
    from backend.services.tool_execution_service import record_user_tool_evidence

    task = find_task_by_id(task_id)
    if not task:
        raise ValueError(f"Task not found: {task_id}")

    result = record_user_tool_evidence(
        task_id,
        tool_name,
        tool_args,
        tool_output,
        note=note,
    )

    task = find_task_by_id(task_id)
    if task:
        normalize_task(task)
        outcome = str(result.get("outcome") or "")
        if task.get("qaEvidence") and outcome != "execution_failed":
            evidence = dict(task["qaEvidence"])
            evidence["userOverride"] = True
            evidence["passed"] = True
            task["qaEvidence"] = evidence

    save_current_project_state(project_id=state.CURRENT_PROJECT_ID)
    publish_board_update(task_id, source="inject_evidence")
    return result


def _dev_verification_status(task: Dict[str, Any], step_started: str) -> str:
    """Return none | ran_with_findings | clean for dev verification this step."""
    if task.get("lastCommandDiagnostics"):
        return "ran_with_findings"

    saw_command = False
    for entry in _transcript_entries_since(task, step_started):
        if entry.get("toolName") not in ("run_test", "run_command"):
            continue
        if entry.get("toolSuccess") is False:
            return "ran_with_findings"
        saw_command = True
        output = str(entry.get("toolOutput") or entry.get("content") or "")
        if "[findings exit" in output.lower():
            return "ran_with_findings"

    if saw_command:
        return "clean"
    return "none"


def _dev_has_verification(task: Dict[str, Any], step_started: str) -> bool:
    return _dev_verification_status(task, step_started) != "none"


def dev_gate_blocks_advance(task: Dict[str, Any]) -> tuple[bool, str]:
    """Block dev board advance when no writes, structure gaps, subtasks pending, or lint unresolved."""
    ws = get_workflow_settings()
    try:
        from backend.services.implementer_profile import implementer_gate_relaxation_active

        relaxed = implementer_gate_relaxation_active(ws)
    except Exception:
        relaxed = False

    from backend.services.focus_slice import (
        all_focus_slices_done,
        dev_micro_steps_enabled,
        focus_cap_reached,
    )
    from backend.services.subtask_service import subtask_gate_blocks_advance

    if dev_micro_steps_enabled(task) and not all_focus_slices_done(task):
        if focus_cap_reached(task):
            return (
                True,
                "Focus step cap reached with unfinished slices — split or recover the card before QA.",
            )
    blocked, reason = subtask_gate_blocks_advance(task)
    if blocked:
        return blocked, reason
    if not _task_has_write_files(task):
        return (
            True,
            "No files written — stay In Progress until apply_patch/write_file.",
        )
    from backend.services.file_completeness import completeness_gate_blocks

    blocked_complete, complete_reason = completeness_gate_blocks(task)
    if blocked_complete:
        return True, complete_reason
    if not relaxed and ws.get("requireWorkspaceStructure", True):
        try:
            from backend.services.workspace_structure_audit import audit_workspace_structure

            audit = audit_workspace_structure()
            if audit.get("stack") != "unknown" and audit.get("critical"):
                missing = ", ".join(str(m) for m in (audit.get("missing") or [])[:8])
                return (
                    True,
                    f"Workspace structure incomplete ({audit.get('stack')}): MISSING {missing}. "
                    "Create scaffold or fix structure before advancing.",
                )
        except Exception:
            pass
    if not relaxed and not ws.get("requireCleanLint"):
        return False, ""
    if relaxed:
        from backend.services.card_ledger import oracle_blocks_done

        blocked_oracle, oracle_reason = oracle_blocks_done(task)
        if blocked_oracle:
            return True, oracle_reason
        return False, ""
    if not ws.get("requireCleanLint"):
        return False, ""
    diagnostics = task.get("lastCommandDiagnostics") or []
    if diagnostics:
        return True, f"Unresolved lint: {len(diagnostics)} problem(s) — fix before advancing."
    status = _dev_verification_status(task, state.SPRINT_STEP_STARTED_AT or "")
    if status == "ran_with_findings":
        return True, "Lint/test command reported findings — resolve before advancing."
    from backend.services.card_ledger import oracle_blocks_done

    blocked_oracle, oracle_reason = oracle_blocks_done(task)
    if blocked_oracle:
        return True, oracle_reason
    return False, ""


def _audit_dev_verification(task: Dict[str, Any], lane_before: str, task_id: str, step_started: str) -> None:
    if not get_workflow_settings().get("requireDevVerification"):
        return
    lane_after = get_task_lane(task_id) or lane_before
    target = _dev_complete_lane()
    if lane_before != "In Progress" or lane_after != target:
        return
    files = task.get("files") or []
    if not files or not _task_has_work_files(task):
        return
    if _dev_has_verification(task, step_started):
        status = _dev_verification_status(task, step_started)
        if status == "ran_with_findings" and get_workflow_settings().get("requireCleanLint"):
            add_system_log(
                "Developer",
                "warning",
                f"'{task.get('title', task_id)}' has lint findings — requireCleanLint enabled; "
                "moving back to In Progress",
            )
            move_board_stage(task_id, "In Progress")
        return
    add_system_log(
        "Developer",
        "warning",
        f"'{task.get('title', task_id)}' advanced without run_command/run_test — "
        "requireDevVerification is enabled; moving back to In Progress",
    )
    move_board_stage(task_id, "In Progress")


def _append_tasks(tasks: List[Dict[str, Any]]) -> int:
    if not tasks:
        return 0
    append_backlog_tasks(tasks)
    return len(tasks)


def _dev_complete_lane() -> str:
    ws = get_workflow_settings()
    try:
        from backend.services.implementer_profile import implementer_gate_relaxation_active

        if implementer_gate_relaxation_active(ws):
            return "Done"
    except Exception:
        pass
    return "Code Review" if ws.get("requireCodeReview") else "QA"


def _maybe_advance_dev_after_lint_write(
    task_id: str,
    task: Dict[str, Any],
    lane_before: str,
) -> bool:
    """Move In Progress → QA/CR when this step wrote and fix-verify lint is clean."""
    return _maybe_advance_dev_after_writes(
        task_id,
        task,
        lane_before,
        require_lint_clean=True,
        require_verify=False,
        log_reason="lint clean after writes",
    )


def _maybe_advance_dev_after_verify(
    task_id: str,
    task: Dict[str, Any],
    lane_before: str,
) -> bool:
    """Move In Progress → QA/CR when this step wrote and verify passed or was duplicate-skipped."""
    return _maybe_advance_dev_after_writes(
        task_id,
        task,
        lane_before,
        require_lint_clean=False,
        require_verify=True,
        log_reason="verify passed after writes",
    )


def _log_lane_advance_event(kind: str, message: str) -> None:
    try:
        from backend.services.step_diagnostics import log_event

        log_event(kind, message)
    except Exception:
        pass


def _maybe_advance_dev_after_writes(
    task_id: str,
    task: Dict[str, Any],
    lane_before: str,
    *,
    require_lint_clean: bool,
    require_verify: bool,
    log_reason: str,
) -> bool:
    del lane_before
    if get_task_lane(task_id) != "In Progress":
        _log_lane_advance_event("lane_advance_skipped", "not_in_progress")
        return False
    lint_clean = bool(
        getattr(state, "FIX_VERIFY_LINT_CLEAN", False) or task.get("fixVerifyLintClean")
    )
    if require_lint_clean and not lint_clean:
        _log_lane_advance_event("lane_advance_skipped", "lint_dirty")
        return False
    from backend.services.card_ledger import oracle_blocks_done

    blocked_oracle, oracle_reason = oracle_blocks_done(task)
    if blocked_oracle:
        _log_lane_advance_event("lane_advance_skipped", "oracle_fail")
        add_system_log("Developer", "warning", f"{task_id}: {oracle_reason}")
        return False
    writes = 0
    tools_log: list = []
    try:
        from backend.services.step_diagnostics import _write_tools_succeeded, get_active_trace

        trace = get_active_trace()
        if trace:
            tools_log = getattr(trace, "tools_log", None) or []
            if _write_tools_succeeded(tools_log):
                writes = 1
    except Exception:
        writes = 0
    if writes <= 0:
        _log_lane_advance_event("lane_advance_skipped", "no_writes")
        return False
    if require_verify:
        from backend.services.duplicate_tool_policy import verify_known_for_advance

        if not verify_known_for_advance(
            tools_log,
            getattr(state, "LAST_AGENT_STEP_RESULT", None),
        ):
            _log_lane_advance_event("lane_advance_skipped", "no_verify_in_tools_log")
            return False
    from backend.services.focus_slice import should_block_lane_advance_for_focus
    from backend.services.subtask_service import subtask_gate_blocks_advance

    if should_block_lane_advance_for_focus(task):
        _log_lane_advance_event("lane_advance_skipped", "focus_slice")
        return False
    blocked, reason = subtask_gate_blocks_advance(task)
    if blocked:
        add_system_log("Developer", "warning", f"{task_id}: {reason}")
        _log_lane_advance_event("lane_advance_skipped", f"subtasks:{reason[:80]}")
        return False
    target = _dev_complete_lane()
    clear_qa_failure(task_id)
    move_board_stage(task_id, target)
    add_system_log(
        "Developer",
        "info",
        f"{task_id}: {log_reason} — advancing to {target} (no update_board)",
    )
    publish_activity(
        task_id,
        "lane_advanced",
        f"Orchestrator moved card to {target} after {log_reason}",
        role="system",
        agent="Developer",
        lane=target,
    )
    _log_lane_advance_event("lane_advance", f"target={target}; {log_reason}")
    return True


def _log_sprint_step_outcome(
    agent: str,
    task_id: str,
    title: str,
    lane_before: str,
    result: str,
) -> None:
    lane_after = get_task_lane(task_id) or lane_before
    snippet = result[:120].replace("\n", " ")
    log_type = "info"
    if result == "SIMULATION_FALLBACK":
        log_type = "warning"
    elif result.startswith("Max tool iterations") or result.startswith("Stopped:"):
        log_type = "warning"
    add_system_log(
        agent,
        log_type,
        f"'{title}' ({task_id}): {lane_before} → {lane_after} — {snippet}",
    )


def _count_task_tool_failures(task: Dict[str, Any]) -> int:
    count = 0
    for entry in task.get("transcript") or []:
        if not isinstance(entry, dict):
            continue
        if entry.get("toolSuccess") is False:
            count += 1
            continue
        if entry.get("role") == "tool":
            content = str(entry.get("content", ""))
            if "✗" in content or " FAILED " in content.upper():
                count += 1
    for decision in task.get("decisions") or []:
        if isinstance(decision, dict) and decision.get("type") == "tool_fail":
            count += 1
    return count


def _audit_dev_files_written(task: Dict[str, Any], lane_before: str, task_id: str) -> None:
    lane_after = get_task_lane(task_id) or lane_before
    if lane_before != "In Progress" or lane_after == lane_before:
        return
    if lane_after in ("Needs PO", "Needs User"):
        return
    if _task_has_write_files(task):
        return
    failures = _count_task_tool_failures(task)
    title = task.get("title", task_id)
    if failures:
        add_system_log(
            "Developer",
            "warning",
            f"Developer advanced '{title}' with no files written — "
            f"{failures} failed tool(s) in transcript; moving back to In Progress "
            "(open task → Transcript, red entries)",
        )
    else:
        add_system_log(
            "Developer",
            "warning",
            f"Developer advanced '{title}' with no files written — "
            "moving back to In Progress (open task → Transcript and Agent Decisions)",
        )
    move_board_stage(task_id, "In Progress")


def _llm_iterations(task: Optional[Dict[str, Any]] = None) -> int:
    base = int(get_workflow_settings().get("maxLlmIterationsPerStep", 8))
    if isinstance(task, dict):
        lsp = task.get("lastStepProgress") or {}
        if isinstance(lsp, dict):
            text_rej = int(lsp.get("textRejections") or 0)
            if text_rej > 5:
                return max(4, base // 2)
    return base


def _commit_on_done(task: Dict[str, Any]) -> None:
    ws_dir = state.WORKSPACE_DIR
    if not os.path.isdir(os.path.join(ws_dir, ".git")):
        git_init()
    msg = f"{task['id']}: {task['title']}"
    result = git_commit(msg)
    if result.get("success") and result.get("hash"):
        record_task_git_commit(
            task["id"],
            {
                "hash": result["hash"],
                "message": msg,
                "remoteUrl": result.get("remoteUrl"),
            },
        )
        add_system_log("System", "success", f"Git commit on Done: {msg} ({result['hash'][:8]})")
    elif result.get("success"):
        add_system_log("System", "success", f"Git commit on Done: {msg}")
    else:
        add_system_log("System", "info", f"Git commit skipped/failed: {result.get('stderr', '')[:200]}")


def complete_dev_offline_simulation(
    active_task: Dict[str, Any],
    file_path: str,
    *,
    write_content: Optional[str] = None,
) -> None:
    if write_content is not None:
        write_workspace_file(file_path, write_content)
        msg = f"Offline fallback wrote {file_path}"
    else:
        msg = f"Used existing workspace file {file_path}"
    clear_qa_failure(active_task["id"])
    move_board_stage(active_task["id"], _dev_complete_lane())
    record_task_decision(active_task["id"], "Developer", "completion", msg)


def _simulate_dev_work(active_task: Dict[str, Any]) -> None:
    from backend.services.simulation_gate import dev_simulation_target

    file_name, stub, existing = dev_simulation_target(active_task)
    if existing is not None:
        complete_dev_offline_simulation(active_task, file_name, write_content=None)
    else:
        complete_dev_offline_simulation(active_task, file_name, write_content=stub)


def _simulate_code_review(active_task: Dict[str, Any]) -> None:
    if random.random() > 0.20:
        move_board_stage(active_task["id"], "QA")
        record_task_decision(active_task["id"], "Code Reviewer", "review", "Offline review PASSED")
    else:
        move_board_stage(active_task["id"], "In Progress")
        record_task_decision(active_task["id"], "Code Reviewer", "review", "Offline review FAILED")


def _simulate_qa(active_task: Dict[str, Any]) -> None:
    if random.random() > 0.15:
        move_board_stage(active_task["id"], "Done")
        _commit_on_done(active_task)
        record_task_decision(active_task["id"], "QA Tester", "qa", "Offline QA PASSED")
    else:
        set_qa_failure(active_task["id"], "Offline QA validation failed", "Simulated test failure")
        move_board_stage(active_task["id"], "In Progress")
        record_task_decision(active_task["id"], "QA Tester", "qa_fail", "Offline QA FAILED")


def _po_backlog_output_is_markdown_outline(po_output: str) -> bool:
    from backend.services.feature_service import looks_like_usable_plan_epics

    if not po_output or not str(po_output).strip():
        return False
    return looks_like_usable_plan_outline(po_output) and not looks_like_usable_plan_epics(po_output)


def _append_po_backlog_from_output(po_output: str, existing: set[str]) -> int:
    """Parse PO epic-grouped (or legacy flat) output and create Features + children."""
    del existing  # titles checked via same-request reuse on spawn
    if not po_output:
        return 0
    try:
        result = apply_plan_epics_from_po_output(po_output)
    except ValueError as e:
        add_system_log("Product Owner", "error", f"Failed to parse PO plan output: {e}")
        return 0
    epic_n = int(result.get("epicCount") or 0)
    child_n = int(result.get("childCount") or 0)
    reused = result.get("reusedEpicIds") or []
    msg = f"PO created {epic_n} epic(s) with {child_n} child card(s)."
    if reused:
        msg += f" Reused {len(reused)} existing epic(s)."
    add_system_log("Product Owner", "success", msg)
    return child_n


def _try_fallback_epics_from_outline(outline_text: str, existing_set: set[str]) -> int:
    from backend.services.feature_service import build_epics_json_from_plan_outline

    fallback_json = build_epics_json_from_plan_outline(outline_text)
    if not fallback_json:
        return 0
    add_system_log(
        "Product Owner",
        "warning",
        "Created cards from outline — LLM did not return valid epics JSON.",
    )
    return _append_po_backlog_from_output(fallback_json, existing_set)


def _finish_po_plan_backlog(po_output: str, outline_text: str, existing_set: set[str]) -> int:
    from backend.services.feature_service import looks_like_usable_plan_epics

    if po_output and looks_like_usable_plan_epics(po_output):
        return _append_po_backlog_from_output(po_output, existing_set)

    if po_output and _po_backlog_output_is_markdown_outline(po_output):
        add_system_log(
            "Product Owner",
            "error",
            "Generate Features failed — model returned a markdown outline instead of JSON epics. "
            "Retry Generate Features.",
        )
        return 0

    count = _try_fallback_epics_from_outline(outline_text, existing_set)
    if count > 0:
        return count

    if po_output:
        return _append_po_backlog_from_output(po_output, existing_set)
    return 0


def run_po_plan_outline(brief: str, ollama_url: str) -> str:
    """Generate a markdown plan outline (phase 1) without creating backlog cards."""
    from backend.services.events import publish_event
    from backend.services.prompt_budget import truncate_brief

    brief = resolve_brief_for_sprint(brief)
    agent_po.ollama_url = ollama_url
    normalize_board_lanes(state.SHARED_BOARD)
    ws = get_workflow_settings()
    num_ctx = int(ws.get("numCtx") or 8192)
    brief_text = truncate_brief(brief, num_ctx)
    publish_event("plan_chunk", {"phase": "start"})
    add_system_log("Product Owner", "info", "Generating project plan outline…")

    outline = ""
    try:
        set_active_sprint_context(PLANNING_OUTLINE_TASK_ID, "Product Owner")
        outline = agent_po.execute_step(
            "Produce a concise markdown project plan ONLY — no JSON, no code, no XML tool tags.\n"
            "If you need to inspect the workspace, use native tool calls (list_dir, glob_file_search, read_file), "
            "then reply with the markdown plan.\n"
            "Sections: ## Summary, ## Approach, ## Risks, ## Open questions, ## Proposed epics.\n"
            f"{po_planning_guidance_block()}"
            + (
                "Under ## Proposed epics, list 6–12 product epics (one line each).\n"
                if is_local_slm_profile()
                else "Under ## Proposed epics, list many concrete product epics as a bullet list "
                "(prefer 6–12 for a non-trivial brief). Each bullet: one line with capability + why. "
                "Do not collapse the brief into a few audit/meta mega-epics.\n"
            )
            + f"{build_dod_block()}\nProject brief:\n{brief_text}",
            max_iterations=max(4, _llm_iterations()),
        )
    finally:
        clear_active_sprint_context()

    if _is_llm_call_failed(outline) or _result_is_max_iterations(outline):
        _log_llm_call_failed(outline or "Max tool iterations reached.", "Plan outline failed —")
        publish_event("plan_chunk", {"phase": "done", "outline": ""})
        return ""

    if outline == "SIMULATION_FALLBACK":
        from backend.services.simulation_gate import (
            build_proposal,
            preview_po_plan_outline,
            try_defer_simulation,
        )

        preview = preview_po_plan_outline()
        prop = build_proposal(
            kind="po_plan_outline",
            task_id="planning",
            agent="Product Owner",
            title="Plan outline",
            summary="Apply offline plan outline stub to project",
            default_preview=preview,
            source="po_plan_outline",
            last_chat_error=_po_last_chat_error(),
        )
        if try_defer_simulation(prop):
            outline = ""
        else:
            outline = (
                "## Summary\nOffline plan stub.\n\n## Approach\nScaffold core modules first.\n\n"
                "## Risks\nUnknown integration points.\n\n## Open questions\n(none)\n\n"
                "## Proposed epics\n"
                "- Project setup — workspace, tooling, and base deps so other slices can build\n"
                "- Core data model — entities and persistence for the main domain\n"
                "- Primary list / browse UI — user can view the main collection\n"
                "- Create & edit flows — add and update items with validation\n"
                "- Detail / summary view — inspect a single item or period\n"
                "- Export or sharing — take work out of the app (list, print, or share)\n"
            )

    if outline and not looks_like_usable_plan_outline(outline):
        _log_llm_call_failed(outline[:400], "Plan outline failed — model returned tool markup instead of markdown.")
        publish_event("plan_chunk", {"phase": "done", "outline": ""})
        return ""

    if outline:
        set_project_plan_outline(outline, source="po_plan_outline")
        for block in outline.split("\n\n"):
            stripped = block.strip()
            if stripped:
                publish_event("plan_chunk", {"chunk": stripped + "\n\n"})
        publish_event("plan_chunk", {"phase": "done", "outline": outline})
        add_system_log("Product Owner", "success", "Plan outline ready — review before generating backlog.")
    return outline


def run_po_plan_backlog(brief: str, ollama_url: str, outline: Optional[str] = None) -> int:
    """Convert an approved plan outline into backlog JSON tasks (phase 2)."""
    brief = resolve_brief_for_sprint(brief)
    agent_po.ollama_url = ollama_url
    normalize_board_lanes(state.SHARED_BOARD)
    existing = existing_backlog_titles()
    existing_set = set(existing)
    existing_hint = ", ".join(existing) if existing else "(none yet)"
    outline_text = coerce_task_text(outline or state.PROJECT_PLAN_OUTLINE or "").strip()
    if not outline_text:
        add_system_log("Product Owner", "warning", "No plan outline — run Plan outline first.")
        return 0

    add_system_log("Product Owner", "info", "Generating Features (epics) + child cards from approved plan…")
    publish_sprint_progress(
        phase="po_plan",
        step=0,
        max_steps=1,
        agent="Product Owner",
        task_id=PLANNING_BACKLOG_TASK_ID,
        task_title="Generating Features from plan…",
        lane="Features",
    )
    po_output = ""
    backlog_iterations = min(6, max(4, _llm_iterations()))
    try:
        set_active_sprint_context(PLANNING_BACKLOG_TASK_ID, "Product Owner")
        po_output = agent_po.execute_step(
            "Do NOT call any tools (no list_dir, read_file, grep, or search). "
            "The full approved plan outline is included below — convert it directly to JSON.\n"
            f"{po_planning_guidance_block()}"
            "Convert the approved plan outline into Features (epics) with smallest developer-ready child cards.\n"
            "Reply with ONLY a JSON object of this shape:\n"
            '{"epics":[{"title":"...","description":"...","children":['
            '{"title":"...","description":"...","acceptanceCriteria":["..."],'
            '"optional blockedBy":[],"optional priority":100,'
            '"optional workType":"implementation","optional requiresDev":true,"optional requiresQa":true}'
            "]}]}\n"
            "Map each Proposed epic to its own Features parent; split vague outline bullets into "
            "multiple epics if they span unrelated concerns.\n"
            "Every epic needs multiple children with testable AC; never emit an epic whose only "
            "child is a one-line dependency bump.\n"
            "Prefer many small children over few large ones.\n"
            f"Existing titles (do NOT duplicate): {existing_hint}\n"
            f"{build_dod_block()}\nApproved plan outline:\n{outline_text}\n\n"
            f"Project brief (context):\n{brief}",
            max_iterations=backlog_iterations,
        )
    finally:
        clear_active_sprint_context()

    if _is_llm_call_failed(po_output) or _result_is_max_iterations(po_output):
        count = _try_fallback_epics_from_outline(outline_text, existing_set)
        if count > 0:
            publish_sprint_progress(
                phase="done",
                step=1,
                max_steps=1,
                agent="Product Owner",
                task_id=PLANNING_BACKLOG_TASK_ID,
                task_title="Features generated from outline",
                lane="Features",
            )
            return count
        _log_llm_call_failed(po_output or "Max tool iterations reached.", "Plan backlog failed —")
        publish_sprint_progress(
            phase="cancelled",
            step=0,
            max_steps=1,
            agent="Product Owner",
            task_id=PLANNING_BACKLOG_TASK_ID,
            task_title="Generate Features failed",
            lane="Features",
        )
        return 0

    if po_output == "SIMULATION_FALLBACK":
        from backend.services.simulation_gate import (
            build_proposal,
            preview_po_backlog_stub,
            try_defer_simulation,
        )

        prop = build_proposal(
            kind="po_backlog",
            task_id="planning",
            agent="Product Owner",
            title="Plan backlog",
            summary="Create offline sample epics and child cards from plan",
            default_preview=preview_po_backlog_stub(),
            source="po_backlog",
            last_chat_error=_po_last_chat_error(),
        )
        if try_defer_simulation(prop):
            publish_sprint_progress(
                phase="cancelled",
                step=0,
                max_steps=1,
                agent="Product Owner",
                task_id=PLANNING_BACKLOG_TASK_ID,
                task_title="Awaiting simulation approval",
                lane="Features",
            )
            return 0

    count = _finish_po_plan_backlog(po_output, outline_text, existing_set)
    if count <= 0:
        publish_sprint_progress(
            phase="cancelled",
            step=0,
            max_steps=1,
            agent="Product Owner",
            task_id=PLANNING_BACKLOG_TASK_ID,
            task_title="Generate Features failed",
            lane="Features",
        )
        return 0

    publish_sprint_progress(
        phase="done",
        step=1,
        max_steps=1,
        agent="Product Owner",
        task_id=PLANNING_BACKLOG_TASK_ID,
        task_title="Features generated",
        lane="Features",
    )
    return count


def run_po_plan(brief: str, ollama_url: str) -> bool:
    max_steps = int(get_workflow_settings().get("maxSprintSteps", 20))
    if state.SPRINT_CANCEL:
        add_system_log("System", "info", "Plan & Run cancelled before PO planning.")
        publish_sprint_progress(
            phase="cancelled",
            step=0,
            max_steps=max_steps,
            agent="Product Owner",
            task_id=PLANNING_TASK_ID,
            task_title="Cancelled",
        )
        return False

    brief = resolve_brief_for_sprint(brief)
    agent_po.ollama_url = ollama_url
    normalize_board_lanes(state.SHARED_BOARD)
    existing = existing_backlog_titles()
    existing_hint = ", ".join(existing) if existing else "(none yet)"

    publish_sprint_progress(
        phase="po_plan",
        step=0,
        max_steps=max_steps,
        agent="Product Owner",
        task_id=PLANNING_TASK_ID,
        task_title="Decomposing brief into epics + child cards…",
        lane="Features",
    )
    add_system_log("Product Owner", "info", "Decomposing project brief into Features (epics)…")

    po_output = ""
    try:
        set_active_sprint_context(PLANNING_TASK_ID, "Product Owner")
        if state.SPRINT_CANCEL:
            add_system_log("System", "info", "Cancel requested — skipping PO Ollama call.")
            return
        add_system_log(
            "Product Owner",
            "info",
            "PO calling Ollama (this may take 1–3 min on first run)…",
        )
        po_output = agent_po.execute_step(
            f"{po_planning_guidance_block()}"
            "Decompose the project brief into Features (epics) with smallest developer-ready child cards.\n"
            "Reply with ONLY a JSON object of this shape:\n"
            '{"epics":[{"title":"...","description":"...","children":['
            '{"title":"...","description":"...","acceptanceCriteria":["..."],'
            '"optional blockedBy":[],"optional priority":100}]}]}\n'
            "Map each product capability to its own Features parent; split vague themes into "
            "multiple epics if they span unrelated concerns.\n"
            "Every epic needs multiple children with testable AC; never emit an epic whose only "
            "child is a one-line dependency bump.\n"
            "Prefer many small children over few large ones.\n"
            f"Existing titles (do NOT duplicate): {existing_hint}\n"
            f"{build_dod_block()}\nProject brief:\n{brief}",
            max_iterations=_llm_iterations(),
        )
        add_system_log("Product Owner", "info", "PO received response, parsing epics…")
    finally:
        clear_active_sprint_context()

    if state.SPRINT_CANCEL:
        add_system_log("System", "info", "Plan & Run cancelled during PO planning.")
        publish_sprint_progress(
            phase="cancelled",
            step=0,
            max_steps=max_steps,
            agent="Product Owner",
            task_id=PLANNING_TASK_ID,
            task_title="Cancelled during PO plan",
        )
        return False

    if _is_llm_call_failed(po_output) or _result_is_max_iterations(po_output):
        _log_llm_call_failed(po_output or "Max tool iterations reached.", "Plan & Run failed —")
        publish_sprint_progress(
            phase="po_plan",
            step=0,
            max_steps=max_steps,
            agent="Product Owner",
            task_id=PLANNING_TASK_ID,
            task_title="LLM call failed — plan not applied",
            lane="Features",
        )
        return False

    if po_output == "SIMULATION_FALLBACK":
        from backend.services.simulation_gate import (
            build_proposal,
            preview_po_backlog_stub,
            try_defer_simulation,
        )

        prop = build_proposal(
            kind="po_backlog",
            task_id="planning",
            agent="Product Owner",
            title="Plan & Run backlog",
            summary="Create offline sample epics and child cards from brief",
            default_preview=preview_po_backlog_stub(),
            source="po_plan",
            last_chat_error=_po_last_chat_error(),
        )
        if try_defer_simulation(prop):
            publish_sprint_progress(
                phase="po_plan",
                step=0,
                max_steps=max_steps,
                agent="Product Owner",
                task_id=PLANNING_TASK_ID,
                task_title="Waiting for offline simulation confirm…",
                lane="Features",
            )
            return False

    if po_output:
        if _po_backlog_output_is_markdown_outline(po_output):
            add_system_log(
                "Product Owner",
                "error",
                "Plan failed — model returned a markdown outline instead of JSON epics. "
                "Use Plan outline then Generate Features, or retry.",
            )
            return False
        _append_po_backlog_from_output(po_output, set(existing))

    publish_sprint_progress(
        phase="po_plan",
        step=0,
        max_steps=max_steps,
        agent="Product Owner",
        task_id=PLANNING_TASK_ID,
        task_title="PO plan complete — epics ready",
        lane="Features",
    )
    return True


def run_po_add_feature(
    title: str,
    description: str,
    ollama_url: str,
    preferred_feature_id: str | None = None,
) -> None:
    append_feature_to_brief(title, description, source="user")
    agent_po.ollama_url = ollama_url
    normalize_board_lanes(state.SHARED_BOARD)
    add_system_log("Product Owner", "info", f"Refining feature '{title}'…")

    existing_features = list_features()
    preferred_id = str(preferred_feature_id or "").strip() or None
    preferred_feature = find_feature_by_id(preferred_id) if preferred_id else None
    if preferred_id and not preferred_feature:
        add_system_log(
            "Product Owner",
            "warning",
            f"preferredFeatureId '{preferred_id}' not found — PO will classify freely",
        )
        preferred_id = None

    feature_context = build_feature_context_for_po(
        {"title": title, "description": description},
        features=existing_features,
    )
    match_hint = ""
    if preferred_id and preferred_feature:
        match_hint = (
            f"\nPreferred feature for this follow-up: {preferred_id} "
            f"({preferred_feature.get('title', '?')}). "
            "Prefer action \"update\" with this featureId unless the request is clearly unrelated.\n"
        )
    elif existing_features:
        probe = {"title": title, "description": description}
        scored: List[tuple[float, str]] = []
        for feat in existing_features:
            score, _ = score_task_similarity(probe, feat)
            if score >= 0.65:
                scored.append((score, str(feat.get("id", ""))))
        if scored:
            scored.sort(reverse=True)
            match_hint = f"\nLikely match (similarity hint): {scored[0][1]} (score {scored[0][0]:.2f})\n"

    intake_prompt = (
        f"{po_planning_guidance_block()}"
        "The user added a feature request. Decide whether this is a NEW feature or an UPDATE to an "
        "existing feature in the Features lane.\n"
        "Reply with ONLY a JSON object (not an array) with:\n"
        "- action: \"new\" or \"update\"\n"
        "- featureId: required when action is \"update\" (must match an existing feature id)\n"
        "- featureTitle: title for the feature parent (updated living spec title)\n"
        "- featureDescription: updated living spec for the feature parent\n"
        "- historySummary: brief note on what changed and why\n"
        "- childTask: { title, description, acceptanceCriteria } — ONE smallest achievable backlog card "
        "for this specific request slice\n\n"
        f"Brief:\n{state.PROJECT_BRIEF}\n\n"
        f"{feature_context}"
        f"{match_hint}"
    )

    po_output = agent_po.execute_step(intake_prompt, max_iterations=_llm_iterations())

    if po_output == "SIMULATION_FALLBACK":
        from backend.services.simulation_gate import build_proposal, try_defer_simulation

        prop = build_proposal(
            kind="feature_intake",
            task_id="planning",
            agent="Product Owner",
            title=title,
            summary="Offline feature intake (new/update epic + child card)",
            default_preview={"title": title},
            source="feature_intake",
            context={
                "title": title,
                "description": description,
                "preferredFeatureId": preferred_id,
            },
        )
        if try_defer_simulation(prop):
            return
        intake_feature_offline(title, description, preferred_feature_id=preferred_id)
    else:
        parsed_obj = extract_json_object_from_text(po_output)
        if parsed_obj:
            intake = parse_po_feature_intake(parsed_obj)
            # When the user pinned an epic, fill missing featureId on update;
            # honour explicit "new" if the PO judged the request unrelated.
            if preferred_id and intake["action"] == "update" and not intake["featureId"]:
                intake["featureId"] = preferred_id
            elif preferred_id and intake["action"] not in ("new", "update"):
                intake["action"] = "update"
                intake["featureId"] = preferred_id
            child_task = intake["childTask"]
            if not child_task.get("acceptanceCriteria"):
                child_task["acceptanceCriteria"] = [child_task.get("description") or title]
            req_title = intake["featureTitle"] or title
            req_desc = intake["featureDescription"] or description
            po_summary = intake["historySummary"] or "PO classified feature intake"
            if intake["action"] == "update" and intake["featureId"]:
                existing = find_feature_by_id(intake["featureId"])
                if existing:
                    feature, child = update_feature(
                        intake["featureId"],
                        title=req_title or str(existing.get("title", "")),
                        description=req_desc or str(existing.get("description", "")),
                        request_title=title,
                        request_body=description,
                        child_task=child_task,
                        po_summary=po_summary,
                    )
                    add_system_log(
                        "Product Owner",
                        "success",
                        f"Updated feature '{feature.get('title')}' — child {child.get('id')}",
                    )
                else:
                    feature, child = create_feature(
                        req_title or title,
                        req_desc or description,
                        request_title=title,
                        request_body=description,
                        child_task=child_task,
                        po_summary=f"{po_summary} (invalid featureId — created new)",
                    )
                    add_system_log(
                        "Product Owner",
                        "warning",
                        f"Unknown featureId — created new feature '{feature.get('title')}'",
                    )
            else:
                feature, child = create_feature(
                    req_title or title,
                    req_desc or description,
                    request_title=title,
                    request_body=description,
                    child_task=child_task,
                    po_summary=po_summary,
                )
                add_system_log(
                    "Product Owner",
                    "success",
                    f"Created feature '{feature.get('title')}' — child {child.get('id')}",
                )
        else:
            try:
                parsed_arr = extract_json_array_from_text(po_output)
                if parsed_arr:
                    raw = parsed_arr[0]
                    child_task = {
                        "title": str(raw.get("title") or title),
                        "description": str(raw.get("description") or description),
                        "acceptanceCriteria": raw.get("acceptanceCriteria")
                        if isinstance(raw.get("acceptanceCriteria"), list)
                        else [description],
                    }
                    if preferred_id and find_feature_by_id(preferred_id):
                        update_feature(
                            preferred_id,
                            title=title,
                            description=description,
                            request_title=title,
                            request_body=description,
                            child_task=child_task,
                            po_summary="Legacy PO array response — updated preferred feature",
                        )
                    else:
                        create_feature(
                            title,
                            description,
                            request_title=title,
                            request_body=description,
                            child_task=child_task,
                            po_summary="Legacy PO array response — created new feature",
                        )
                else:
                    intake_feature_offline(title, description, preferred_feature_id=preferred_id)
            except Exception:
                intake_feature_offline(title, description, preferred_feature_id=preferred_id)

    save_current_project_state(project_id=state.CURRENT_PROJECT_ID)


PO_SPLIT_MAX_ITERATIONS = 5
PO_SPLIT_MAX_STEP_DURATION_SEC = 120


def _deterministic_po_split_stub(task: Dict[str, Any]) -> str:
    """Two-slice backlog JSON when PO split LLM/tool path fails."""
    ac = task.get("acceptanceCriteria") or []
    ac1 = ac[0] if ac else "Deliver first slice of the feature"
    ac2 = ac[1] if len(ac) > 1 else "Deliver remaining scope"
    if not isinstance(ac1, str):
        ac1 = str(ac1)
    if not isinstance(ac2, str):
        ac2 = str(ac2)
    desc = str(task.get("description") or "")[:500]
    title = str(task.get("title") or "Subtask")
    return json.dumps(
        [
            {
                "title": f"{title} (part 1)",
                "description": desc or title,
                "acceptanceCriteria": [ac1],
            },
            {
                "title": f"{title} (part 2)",
                "description": desc or title,
                "acceptanceCriteria": [ac2],
            },
        ]
    )


def _po_split_llm_failed(po_output: Optional[str]) -> bool:
    return not po_output or str(po_output).startswith("LLM_CALL_FAILED:")


def _po_split_invalid_add_backlog_tool_json(task_id: str) -> bool:
    from backend.agents.task_context import find_task_by_id
    from backend.services.tool_json_recovery import is_invalid_tool_json_error

    task = find_task_by_id(task_id)
    if not task:
        return False
    for entry in reversed(task.get("transcript") or []):
        if not isinstance(entry, dict):
            continue
        if entry.get("toolName") != "add_backlog_tasks":
            continue
        if entry.get("toolSuccess") is not False:
            continue
        err = str(entry.get("error") or entry.get("toolError") or "")
        if is_invalid_tool_json_error(err):
            return True
    return False


def _new_backlog_ids_since(backlog_before_ids: set) -> List[str]:
    return [t["id"] for t in state.SHARED_BOARD.get("Backlog", []) if t["id"] not in backlog_before_ids]


def _collect_po_split_added(
    task_id: str,
    po_output: Optional[str],
    backlog_before_ids: set,
) -> tuple[int, List[str]]:
    if _po_chat_used_add_backlog_tool(task_id):
        new_task_ids = _new_backlog_ids_since(backlog_before_ids)
        return len(new_task_ids), new_task_ids
    if po_output and po_output != "SIMULATION_FALLBACK" and not _po_split_llm_failed(po_output):
        added = apply_backlog_from_po_response(po_output, task_id)
        new_task_ids = _new_backlog_ids_since(backlog_before_ids)
        return added, new_task_ids
    return 0, []


def _apply_po_split_deterministic_fallback(
    task_id: str,
    task: Dict[str, Any],
    backlog_before_ids: set,
) -> tuple[int, List[str]]:
    try:
        from backend.services.step_diagnostics import log_event

        log_event(
            "po_split_deterministic_fallback",
            f"PO split LLM failed — using 2-slice stub for {task_id}",
        )
    except Exception:
        pass
    stub = _deterministic_po_split_stub(task)
    added = apply_backlog_from_po_response(stub, task_id)
    new_task_ids = _new_backlog_ids_since(backlog_before_ids)
    return added, new_task_ids


def run_po_split_task(task_id: str, ollama_url: str, guidance: str = "") -> Dict[str, Any]:
    """Split a card into subtasks via PO tool call or JSON fallback."""
    task = find_task_by_id(task_id)
    if not task:
        raise ValueError(f"Task not found: {task_id}")

    normalize_task(task)
    agent_po.ollama_url = ollama_url
    set_active_sprint_context(task_id, "Product Owner")
    add_system_log("Product Owner", "info", f"Splitting task '{task.get('title', task_id)}'…")

    try:
        backlog_before_ids = {t["id"] for t in state.SHARED_BOARD.get("Backlog", [])}
        prompt = build_task_prompt(task, state.PROJECT_BRIEF)
        extra = f"\nAdditional guidance: {guidance.strip()}" if guidance.strip() else ""
        split_prompt = (
            f"{po_planning_guidance_block()}{prompt}\n\n"
            "Split this card into 2–5 smaller developer-ready backlog tasks."
            f"{extra}\n"
            "Reply with ONLY a JSON array (no markdown prose). Each item needs "
            "title, description, acceptanceCriteria (≥2 for implementation), scope, testPlan.\n"
            f"Parent task id for relatedTaskIds: {task_id!r}."
        )
        po_output = agent_po.execute_step(
            split_prompt,
            max_iterations=PO_SPLIT_MAX_ITERATIONS,
            json_only_split=True,
            max_step_duration_sec=PO_SPLIT_MAX_STEP_DURATION_SEC,
        )

        added = 0
        new_task_ids: List[str] = []
        if po_output == "SIMULATION_FALLBACK":
            from backend.services.simulation_gate import build_proposal, try_defer_simulation

            split_stub = _deterministic_po_split_stub(task)
            prop = build_proposal(
                kind="po_split",
                task_id=task_id,
                agent="Product Owner",
                title=str(task.get("title") or task_id),
                summary="Split card into offline sample subtasks",
                default_preview={"subtaskCount": 2},
                source="po_split",
                context={"poOutput": split_stub},
            )
            if try_defer_simulation(prop):
                added = 0
            else:
                added, new_task_ids = _apply_po_split_deterministic_fallback(
                    task_id, task, backlog_before_ids
                )
        else:
            added, new_task_ids = _collect_po_split_added(task_id, po_output, backlog_before_ids)
            if added == 0 and (
                _po_split_llm_failed(po_output)
                or _po_split_invalid_add_backlog_tool_json(task_id)
            ):
                added, new_task_ids = _apply_po_split_deterministic_fallback(
                    task_id, task, backlog_before_ids
                )

        parent = find_task_by_id(task_id)
        split_valid = bool(
            added > 0
            and get_task_lane(task_id) == "Done"
            and parent
            and parent.get("splitSuperseded")
        )
        if not split_valid:
            added = 0
            new_task_ids = []
        save_current_project_state(project_id=state.CURRENT_PROJECT_ID)
        publish_board_update(task_id, source="split")
        add_system_log(
            "Product Owner",
            "success" if added else "warning",
            f"Split {task_id}: added {added} subtask(s)" if added else f"Split {task_id}: no subtasks added",
        )
        return {
            "added": added,
            "taskId": task_id,
            "taskIds": new_task_ids,
            "parentDone": bool(split_valid),
        }
    finally:
        clear_active_sprint_context()


def _po_clarification_retry_prompt_block(task: Dict[str, Any]) -> str:
    """Surface prior incomplete PO replies so the next step is not a blank slate."""
    attempts: List[Dict[str, Any]] = []
    for decision in reversed(task.get("decisions") or []):
        if not isinstance(decision, dict):
            continue
        if decision.get("agent") != "Product Owner":
            continue
        if decision.get("type") not in ("clarification", "clarification_incomplete"):
            continue
        attempts.append(decision)
        if len(attempts) >= 2:
            break
    if not attempts:
        return ""
    lines = [
        "\n=== PRIOR PO CLARIFICATION ATTEMPTS (incomplete — do not repeat verbatim) ===",
        "The Developer is blocked until you provide clarification JSON and move the card to In Progress.",
    ]
    for decision in reversed(attempts):
        body = str(decision.get("detail") or decision.get("summary") or "").strip()
        if not body:
            continue
        ts = decision.get("timestamp") or "?"
        lines.append(f"[{ts}] {body[:1500]}")
    lines.append(
        "Required now: JSON with description + acceptanceCriteria (+ optional briefAddition), "
        "then update_board → In Progress. Do not output Developer implementation steps.\n"
    )
    return "\n".join(lines)


def _apply_po_clarification_result(active_task: Dict[str, Any], result: str) -> bool:
    from backend.services.po_clarification import apply_clarification_from_text

    return apply_clarification_from_text(active_task["id"], result)


def _apply_refinement_dev_result(task: Dict[str, Any], result: str) -> bool:
    """Parse dev refinement JSON and update task fields."""
    obj = extract_json_object_from_text(result)
    if not obj:
        return False
    ready = bool(obj.get("ready"))
    questions = obj.get("questions") or []
    if isinstance(questions, str):
        questions = [questions]
    notes = coerce_task_text(obj.get("explorationNotes") or obj.get("exploration_notes") or "")
    needs_spike = bool(obj.get("needsSpike") or obj.get("needs_spike"))
    spike_objective = coerce_task_text(
        obj.get("spikeObjective") or obj.get("spike_objective") or notes or ""
    )
    if needs_spike and spike_objective:
        create_spike_task(task, spike_objective)
        task["refinementRoundTrips"] = int(task.get("refinementRoundTrips") or 0) + 1
        record_task_decision(
            task["id"],
            "Developer",
            "refinement_spike",
            f"Spike requested: {spike_objective[:120]}",
            result[:500],
        )
        return True
    task["refinementDevReady"] = ready
    task["refinementQuestions"] = [
        coerce_task_text(q).strip() for q in questions if coerce_task_text(q).strip()
    ]
    if notes:
        existing = coerce_task_text(task.get("refinementNotes") or "")
        task["refinementNotes"] = f"{existing}\n{notes}".strip() if existing else notes
    task["refinementStatus"] = "dev_reviewed"
    task["refinementRoundTrips"] = int(task.get("refinementRoundTrips") or 0) + 1
    record_task_decision(
        task["id"],
        "Developer",
        "refinement_dev",
        "Ready for implementation" if ready else f"{len(task['refinementQuestions'])} question(s)",
        result[:500],
    )
    return True


def _run_refinement_dev_review(active_task: Dict[str, Any], brief: str) -> None:
    task_id = active_task["id"]
    lane_before = get_task_lane(task_id) or "Refinement"
    ws = get_workflow_settings()
    max_rounds = int(ws.get("maxRefinementRoundTrips") or 3)
    set_active_sprint_context(task_id, "Developer")
    try:
        from backend.services.backup_model import apply_model_for_step

        apply_model_for_step(agent_dev, "dev", find_task_by_id(task_id) or active_task)
    except Exception:
        pass
    state.REFINEMENT_MODE = True
    add_system_log("Developer", "info", f"Refinement review for '{active_task['title']}'…")
    questions_block = ""
    if active_task.get("refinementQuestions"):
        qs = "\n".join(f"- {q}" for q in active_task["refinementQuestions"])
        questions_block = f"\nPrevious questions (PO may have updated AC):\n{qs}\n"
    prompt = (
        build_task_prompt(active_task, brief)
        + questions_block
        + "\nREFINEMENT ONLY — do not implement. Explore the codebase with read-only tools "
        "(read_file, grep, glob_file_search, search_code, git_status, git_diff). "
        "Do NOT use write_file, apply_patch, run_command, or git_commit.\n"
        "DECOMPOSE CHECK FIRST: Before marking ready, decide if this card mixes unrelated "
        "deliverables or is too large for one implementation pass. If so, return "
        '{"ready": false, "questions": ["..."], "explorationNotes": "..."} recommending a split '
        "into distinct components (PO should use add_backlog_tasks or add_subtasks) — do not "
        "ask vague AC questions while the scope itself is too broad.\n"
        "Reply with a JSON object:\n"
        '{"ready": true} when acceptance criteria are sufficient to implement as one focused card, OR\n'
        '{"ready": false, "questions": ["..."], "explorationNotes": "..."} when clarification or a split is needed, OR\n'
        '{"ready": false, "needsSpike": true, "spikeObjective": "...", "explorationNotes": "..."} '
        "when technical unknowns require a dedicated spike exploration first.\n"
        "If blocked after repeated rounds, use update_board to move to 'Needs PO'."
    )
    try:
        result = agent_dev.execute_step(prompt, max_iterations=_llm_iterations())
    finally:
        state.REFINEMENT_MODE = False

    with state.STATE_LOCK:
        task = find_task_by_id(task_id)
        if not task:
            return
        if result == "SIMULATION_FALLBACK":
            from backend.services.simulation_gate import (
                build_proposal,
                mark_step_outcome_simulation_pending,
                try_defer_simulation,
            )

            prop = build_proposal(
                kind="refinement_dev",
                task_id=task_id,
                agent="Developer",
                title=str(task.get("title") or task_id),
                summary="Mark refinement dev-reviewed (offline)",
                default_preview={"refinementDevReady": True},
                source="refinement_dev",
            )
            if try_defer_simulation(prop):
                mark_step_outcome_simulation_pending(task_id, "Developer", lane_before)
            else:
                task["refinementDevReady"] = True
                task["refinementStatus"] = "dev_reviewed"
                task["refinementRoundTrips"] = int(task.get("refinementRoundTrips") or 0) + 1
        else:
            record_task_decision(task_id, "Developer", "refinement_dev", result[:500], result)
            if not _apply_refinement_dev_result(task, result):
                add_system_log(
                    "Developer",
                    "warning",
                    f"Refinement review incomplete for '{task['title']}' — missing JSON",
                )
        rounds = int(task.get("refinementRoundTrips") or 0)
        if (
            not task.get("refinementDevReady")
            and rounds >= max_rounds
            and _task_in_lane(task_id, "Refinement")
        ):
            move_board_stage(task_id, "Needs PO")
            task["refinementStatus"] = "blocked"
            record_task_decision(
                task_id,
                "System",
                "escalation",
                f"Max refinement rounds ({max_rounds}) — escalated to PO",
            )
            publish_activity(
                task_id,
                "refinement_escalated",
                "Max refinement rounds reached — moved to Needs PO",
                role="assistant",
                agent="System",
                lane="Needs PO",
            )
        _check_stuck_and_escalate(task_id, lane_before, agent_key="dev")


def _apply_spike_result(spike_task: Dict[str, Any], result: str) -> bool:
    """Parse spike JSON and merge findings into the parent refinement card."""
    obj = extract_json_object_from_text(result)
    if not obj:
        return False
    findings = coerce_task_text(obj.get("findings") or "")
    recommendations = coerce_task_text(obj.get("recommendations") or "")
    open_questions = obj.get("openQuestions") or obj.get("open_questions") or []
    if isinstance(open_questions, str):
        open_questions = [open_questions]
    report = {
        "findings": findings,
        "recommendations": recommendations,
        "openQuestions": [coerce_task_text(q).strip() for q in open_questions if coerce_task_text(q).strip()],
    }
    spike_task["spikeReport"] = json.dumps(report, ensure_ascii=False)
    spike_task["spikeStatus"] = "complete"
    spike_task["refinementStatus"] = "ready"

    parent_id = str(spike_task.get("spikeForTaskId") or "")
    parent = find_task_by_id(parent_id) if parent_id else None
    if not parent:
        return True

    report_block = (
        f"## Spike findings\n{findings}\n\n## Recommendations\n{recommendations}".strip()
    )
    existing = coerce_task_text(parent.get("refinementNotes") or "")
    parent["refinementNotes"] = f"{existing}\n\n{report_block}".strip() if existing else report_block
    if report["openQuestions"]:
        parent["refinementQuestions"] = report["openQuestions"]
    parent["needsSpike"] = False
    parent["refinementStatus"] = "pending"
    parent["refinementDevReady"] = False
    record_task_decision(
        parent_id,
        "Developer",
        "spike_complete",
        f"Spike complete — {len(report['openQuestions'])} open question(s)",
        result[:500],
    )
    return True


def _run_spike_dev(active_task: Dict[str, Any], brief: str) -> None:
    task_id = active_task["id"]
    parent_id = str(active_task.get("spikeForTaskId") or "")
    lane_before = get_task_lane(task_id) or "Refinement"
    objective = coerce_task_text(active_task.get("spikeObjective") or active_task.get("description") or "")
    set_active_sprint_context(task_id, "Developer")
    state.REFINEMENT_MODE = True
    add_system_log("Developer", "info", f"Spike exploration for '{active_task['title']}'…")
    parent = find_task_by_id(parent_id) if parent_id else None
    parent_block = ""
    if parent:
        parent_block = (
            f"\nParent refinement card: {parent.get('title')}\n"
            f"Acceptance criteria:\n"
            + "\n".join(f"- {c}" for c in (parent.get("acceptanceCriteria") or []))
            + "\n"
        )
    prompt = (
        build_task_prompt(active_task, brief)
        + parent_block
        + f"\nSPIKE OBJECTIVE:\n{objective}\n"
        + "\nSPIKE ONLY — read-only exploration (read_file, grep, glob_file_search, search_code, "
        "git_status, git_diff). Do NOT modify files or run destructive commands.\n"
        "Reply with ONLY a JSON object:\n"
        '{"findings": "...", "recommendations": "...", "openQuestions": ["..."]}'
    )
    try:
        with state.STATE_LOCK:
            task = find_task_by_id(task_id)
            if task:
                task["spikeStatus"] = "running"
        result = agent_dev.execute_step(prompt, max_iterations=_llm_iterations())
    finally:
        state.REFINEMENT_MODE = False

    with state.STATE_LOCK:
        spike = find_task_by_id(task_id)
        if not spike:
            return
        if result == "SIMULATION_FALLBACK":
            from backend.services.simulation_gate import (
                build_proposal,
                mark_step_outcome_simulation_pending,
                try_defer_simulation,
            )

            prop = build_proposal(
                kind="spike",
                task_id=task_id,
                agent="Developer",
                title=str(spike.get("title") or task_id),
                summary="Apply offline spike findings JSON to spike and parent",
                default_preview={"findings": "Offline spike simulation."},
                source="spike",
            )
            if try_defer_simulation(prop):
                mark_step_outcome_simulation_pending(task_id, "Developer", lane_before)
            else:
                spike["spikeReport"] = json.dumps(
                    {"findings": "Offline spike simulation.", "recommendations": "", "openQuestions": []}
                )
                spike["spikeStatus"] = "complete"
                if parent_id:
                    parent_task = find_task_by_id(parent_id)
                    if parent_task:
                        parent_task["needsSpike"] = False
                        parent_task["refinementStatus"] = "pending"
        else:
            record_task_decision(task_id, "Developer", "spike_dev", result[:500], result)
            if not _apply_spike_result(spike, result):
                add_system_log(
                    "Developer",
                    "warning",
                    f"Spike incomplete for '{spike['title']}' — missing JSON",
                )
        publish_board_update(task_id, source="spike_complete")
        _check_stuck_and_escalate(task_id, lane_before, agent_key="dev")


def _run_refinement_po_update(active_task: Dict[str, Any], brief: str) -> None:
    task_id = active_task["id"]
    lane_before = get_task_lane(task_id) or "Refinement"
    set_active_sprint_context(task_id, "Product Owner")
    try:
        from backend.services.backup_model import apply_model_for_step

        apply_model_for_step(agent_po, "po", find_task_by_id(task_id) or active_task)
    except Exception:
        pass
    add_system_log("Product Owner", "info", f"Refinement update for '{active_task['title']}'…")
    questions = active_task.get("refinementQuestions") or []
    q_block = "\n".join(f"- {q}" for q in questions) if questions else "(none listed)"
    dev_ready = bool(active_task.get("refinementDevReady"))
    prompt = (
        build_task_prompt(active_task, brief)
        + f"\nDeveloper refinement questions:\n{q_block}\n"
        + f"Developer marked ready: {dev_ready}\n"
        "DECOMPOSE FIRST: Before refining AC, decide whether this card should be split into "
        "distinct backlog cards (add_backlog_tasks / split) or ordered subtasks (add_subtasks / "
        "executionPlan). Only then refine description and acceptance criteria on the surviving "
        "parent scope. Do not mark complete while the card still mixes unrelated deliverables.\n"
        "Update description and acceptance criteria. Reply with JSON: "
        '{"description": "...", "acceptanceCriteria": ["..."], "briefAddition": "..."}\n'
        "Use add_backlog_tasks to split scope if needed. "
        "Use add_subtasks with executionOrder to define an ordered todo list under this card. "
        "Or include executionPlan in JSON: "
        '[{"title": "...", "description": "...", "acceptanceCriteria": ["..."], "order": 1}, ...]\n'
        "When refinement is complete and the card is ready for implementation, "
        "use update_board to move to 'Backlog'."
    )
    result = agent_po.execute_step(prompt, max_iterations=_llm_iterations())

    with state.STATE_LOCK:
        task = find_task_by_id(task_id)
        if not task:
            return
        clarified = False
        deferred_sim = False
        if result == "SIMULATION_FALLBACK":
            from backend.services.simulation_gate import (
                build_proposal,
                mark_step_outcome_simulation_pending,
                try_defer_simulation,
            )

            prop = build_proposal(
                kind="refinement_po",
                task_id=task_id,
                agent="Product Owner",
                title=str(task.get("title") or task_id),
                summary="Mark refinement PO-updated (offline)",
                default_preview={"refinementStatus": "po_updated"},
                source="refinement_po",
            )
            if try_defer_simulation(prop):
                deferred_sim = True
                mark_step_outcome_simulation_pending(task_id, "Product Owner", lane_before)
            else:
                clarified = True
        else:
            record_task_decision(task_id, "Product Owner", "refinement_po", result[:500], result)
            clarified = _apply_po_clarification_result(task, result)
            obj = extract_json_object_from_text(result)
            if obj and isinstance(obj.get("executionPlan"), list):
                from backend.services.subtask_service import apply_execution_plan

                apply_execution_plan(task_id, obj["executionPlan"])
        if not deferred_sim:
            task["refinementStatus"] = "po_updated"
            if not clarified:
                add_system_log(
                    "Product Owner",
                    "warning",
                    f"Refinement PO update incomplete for '{task['title']}'",
                )
        if not deferred_sim and _task_in_lane(task_id, "Refinement") and task.get("refinementDevReady"):
            task["refinementComplete"] = True
            task["refinementStatus"] = "ready"
            move_board_stage(task_id, "Backlog")
            sort_backlog()
            publish_activity(
                task_id,
                "refinement_complete",
                "Refinement complete — task moved to Backlog",
                role="assistant",
                agent="Product Owner",
                lane="Backlog",
            )
        elif not deferred_sim and _task_in_lane(task_id, "Backlog") and task.get("refinementComplete"):
            publish_activity(
                task_id,
                "refinement_complete",
                "PO marked refinement complete",
                role="assistant",
                agent="Product Owner",
                lane="Backlog",
            )
        _check_stuck_and_escalate(task_id, lane_before, agent_key="po")


def _po_step_was_truncated(agent: Any) -> bool:
    usage = getattr(agent, "_last_token_usage", None) or {}
    if str(usage.get("doneReason") or "").lower() == "length":
        return True
    try:
        from backend.services.step_diagnostics import get_active_trace

        trace = get_active_trace()
        if trace:
            for call in reversed(trace.ollama_calls or []):
                if not isinstance(call, dict):
                    continue
                if call.get("truncated") or str(call.get("doneReason") or "").lower() == "length":
                    return True
    except Exception:
        pass
    return False


def _run_po_clarification(active_task: Dict[str, Any], brief: str) -> None:
    task_id = active_task["id"]
    lane_before = get_task_lane(task_id) or "Needs PO"
    title = str(active_task.get("title", task_id))
    set_active_sprint_context(task_id, "Product Owner")
    _ensure_step_trace(task_id, title, "Product Owner", lane_before)
    result = ""
    try:
        try:
            from backend.services.backup_model import apply_model_for_step

            apply_model_for_step(agent_po, "po", find_task_by_id(task_id) or active_task)
        except Exception:
            pass
        add_system_log("Product Owner", "info", f"Clarifying '{active_task['title']}'…")
        task_for_prompt = find_task_by_id(task_id) or active_task
        from backend.services.po_clarification import (
            move_off_needs_po,
            po_llm_skip_block_reason,
            po_skip_churn_should_block,
            should_move_off_needs_po_without_llm,
        )

        churn_block, churn_msg = po_skip_churn_should_block(task_for_prompt)
        if churn_block:
            parked = _try_move_to_needs_user(
                task_id,
                task_for_prompt,
                churn_msg,
                kind="po_skip_churn",
            )
            if parked:
                return f"Parked to Needs User: {churn_msg}"
        if should_move_off_needs_po_without_llm(task_for_prompt):
            with state.STATE_LOCK:
                locked = find_task_by_id(task_id)
                if locked:
                    reapply_force_patch_if_dev_stalled(locked)
            dest = move_off_needs_po(task_id)
            result = f"Moved to {dest or 'In Progress'} without a PO generate (spec already present)."
            try:
                from backend.services.sprint_speed_gates import last_step_exit_reason
                from backend.services.step_diagnostics import log_event as _log_ev

                last_exit = last_step_exit_reason(task_for_prompt) or "none"
                live = find_task_by_id(task_id) or task_for_prompt
                force_patch = bool((live or {}).get("forcePatchNextDevStep"))
                _log_ev(
                    "po_llm_skipped",
                    f"spec present; last_exit={last_exit}; forcePatch={force_patch}",
                )
            except Exception:
                pass
            add_system_log(
                "Product Owner",
                "info",
                f"{task_id}: skipped PO LLM — {result}",
            )
            with state.STATE_LOCK:
                task = find_task_by_id(task_id)
                if task:
                    record_task_decision(
                        task_id,
                        "Product Owner",
                        "clarification",
                        result,
                        "Deterministic Needs PO → In Progress; no Ollama turn.",
                    )
            _record_last_step_outcome(
                task_id, lane_before, "Product Owner", agent_result=result
            )
            if dest == "In Progress":
                live = find_task_by_id(task_id)
                if live and not live.get("phaseCycleCapReached"):
                    _run_developer_step(dict(live), brief)
            return
        try:
            from backend.services.step_diagnostics import log_event as _log_ev

            _log_ev(
                "po_llm_started",
                po_llm_skip_block_reason(task_for_prompt) or "clarification needed",
            )
        except Exception:
            pass
        prompt = (
            build_task_prompt(task_for_prompt, brief)
            + _po_clarification_retry_prompt_block(task_for_prompt)
            + "\nDeveloper needs clarification. Reply with a JSON object: "
            '{"description": "...", "acceptanceCriteria": ["..."], "briefAddition": "..."}\n'
            "Then use update_board to move back to 'In Progress' "
            "(optional description/acceptanceCriteria on that call). "
            "Valid JSON alone is enough — do not restate it after the board moves."
        )
        result = agent_po.execute_step(prompt, max_iterations=_llm_iterations())

        with state.STATE_LOCK:
            task = find_task_by_id(task_id)
            if not task:
                return
            clarified = False
            deferred_sim = False
            if result == "SIMULATION_FALLBACK":
                from backend.services.simulation_gate import (
                    build_proposal,
                    mark_step_outcome_simulation_pending,
                    try_defer_simulation,
                )

                prop = build_proposal(
                    kind="po_clarification",
                    task_id=task_id,
                    agent="Product Owner",
                    title=str(task.get("title") or task_id),
                    summary="Record offline PO clarification decision",
                    default_preview={"note": "Offline clarification"},
                    source="po_clarification",
                )
                if try_defer_simulation(prop):
                    deferred_sim = True
                    mark_step_outcome_simulation_pending(task_id, "Product Owner", lane_before)
                else:
                    record_task_decision(task_id, "Product Owner", "clarification", "Offline clarification")
                    from backend.services.po_clarification import move_off_needs_po

                    move_off_needs_po(task_id)
            else:
                obj = extract_json_object_from_text(result)
                if obj and (obj.get("description") or obj.get("acceptanceCriteria")):
                    record_task_decision(task_id, "Product Owner", "clarification", result[:500], result)
                elif result and result.strip():
                    record_task_decision(
                        task_id,
                        "Product Owner",
                        "clarification_incomplete",
                        result[:500],
                        result,
                    )
            if not deferred_sim:
                from backend.services.po_clarification import complete_needs_po_clarification

                clarified, note = complete_needs_po_clarification(task_id, text=result)
                dest = get_task_lane(task_id) or ""
                if dest in ("In Progress", "Refinement") and dest != lane_before:
                    publish_activity(
                        task_id,
                        "po_clarified",
                        (
                            "PO clarified refinement blockers — returned to Refinement"
                            if dest == "Refinement"
                            else "PO clarified requirements and returned task to Dev"
                        ),
                        role="assistant",
                        agent="Product Owner",
                        lane=dest,
                    )
                elif not clarified and _task_in_lane(task_id, "Needs PO"):
                    live = find_task_by_id(task_id)
                    from backend.services.po_clarification import task_has_ready_spec

                    if (
                        live
                        and task_has_ready_spec(live)
                        and _po_step_was_truncated(agent_po)
                    ):
                        from backend.services.board_service import prepare_task_for_dev_claim

                        prepare_task_for_dev_claim(live)
                        reapply_force_patch_if_dev_stalled(live)
                        from backend.services.po_clarification import move_off_needs_po

                        dest = move_off_needs_po(task_id)
                        if dest and dest != "Needs PO":
                            clarified = True
                            add_system_log(
                                "Product Owner",
                                "info",
                                f"PO turn truncated with ready spec — moved {task_id} to {dest}",
                            )
                    if not clarified:
                        add_system_log(
                            "Product Owner",
                            "warning",
                            f"Clarification incomplete for '{task['title']}' — {note}",
                        )
            _record_last_step_outcome(
                task_id, lane_before, "Product Owner", agent_result=result or ""
            )
            _check_stuck_and_escalate(task_id, lane_before, agent_key="po")
    finally:
        if not (
            isinstance(state.LAST_STEP_OUTCOME, dict)
            and state.LAST_STEP_OUTCOME.get("taskId") == task_id
        ):
            _finalize_role_step_diagnostics(
                task_id, lane_before, "Product Owner", agent_result=result or None
            )


def _run_developer_step(active_task: Dict[str, Any], brief: str) -> None:
    task_id = active_task["id"]
    lane_before = get_task_lane(task_id) or "In Progress"
    title = str(active_task.get("title", task_id))
    if lane_before == "Needs PO":
        add_system_log(
            "System",
            "warning",
            f"{task_id}: skipping Developer — card is in Needs PO (no begin_dev_step)",
        )
        live = find_task_by_id(task_id) or active_task
        if live.get("phaseCycleCapReached"):
            _recover_latched_dev_card(dict(live), brief)
        return
    step_started = _mark_sprint_step_start()
    set_active_sprint_context(task_id, "Developer")
    live_task = find_task_by_id(task_id) or active_task
    from backend.services.sprint_speed_gates import (
        begin_dev_step,
        empty_gen_should_skip,
        identical_write_loop_should_park,
        no_write_stall_should_park,
        same_next_task_should_park,
    )

    if identical_write_loop_should_park(live_task) and not _forced_patch_retry_allowed(live_task):
        park_msg = (
            "Identical write loop — skipping another Developer rewrite of the same file."
        )
        add_system_log("System", "warning", f"{task_id}: {park_msg}")
        _handle_visit_cap_stall(
            task_id,
            live_task,
            park_msg,
            title=title,
            lane_before=lane_before,
            brief=brief,
        )
        return
    if same_next_task_should_park(live_task) and not _forced_patch_retry_allowed(live_task):
        park_msg = (
            "Same next task reissued with no writes or better oracle — parking instead of another generate."
        )
        add_system_log("System", "warning", f"{task_id}: {park_msg}")
        _handle_visit_cap_stall(
            task_id,
            live_task,
            park_msg,
            title=title,
            lane_before=lane_before,
            brief=brief,
        )
        return
    if empty_gen_should_skip(live_task):
        try:
            from backend.services.step_diagnostics import log_event

            log_event("empty_gen_skip", "GPU cool-off after empty generation")
        except Exception:
            pass
        add_system_log(
            "System",
            "warning",
            f"{task_id}: skipping Developer — empty-generation GPU cool-off",
        )
        return
    if no_write_stall_should_park(live_task) and int(state.SPRINT_PROGRESS_MAX or 1) != 1:
        add_system_log(
            "System",
            "warning",
            f"{task_id}: skipping Developer — consecutive explore/duplicate stalls with no write",
        )
        if is_lint_wall_card(live_task) and live_task.get("forcePatchNextDevStep"):
            _handle_lint_stuck(task_id, dict(live_task), park_msg="lint stall — file blocker or Forced Patch retry")
            _record_dev_precheck_skip(
                task_id,
                title,
                lane_before,
                reason="Lint stall — Forced Patch retry queued",
            )
            return
        _recover_latched_dev_card(dict(live_task), brief)
        return
    visit, capped = begin_dev_step(live_task)
    if capped:
        result = (
            f"Stopped: phase cycle cap reached at Developer visit {visit}. "
            "This card must be split, clarified, or explicitly reset before Dev runs again."
        )
        add_system_log("System", "warning", f"{task_id}: {result}")
        with state.STATE_LOCK:
            task = find_task_by_id(task_id) or live_task
            record_task_decision(
                task_id,
                "System",
                "phase_cycle_cap",
                result,
                str(task.get("phaseCycleCapReason") or result),
            )
            task["stuckLoops"] = max(
                int(task.get("stuckLoops") or 0),
                max(1, int(get_workflow_settings().get("maxStuckSteps") or 3)) - 1,
            )
        _record_dev_precheck_skip(task_id, title, lane_before, reason=result)
        with state.STATE_LOCK:
            _check_stuck_and_escalate(task_id, lane_before, agent_key="dev")
        live = find_task_by_id(task_id) or live_task
        if live and (
            not is_lint_wall_card(live) or int(live.get("lintUnlatchCount") or 0) >= 1
        ):
            _recover_latched_dev_card(dict(live), brief)
        return
    try:
        from backend.services.backup_model import apply_model_for_step

        model_in_use = apply_model_for_step(
            agent_dev, "dev", find_task_by_id(task_id) or active_task
        )
    except Exception:
        model_in_use = str(getattr(agent_dev, "model", "") or "")
    try:
        from backend.services.workspace_scaffold import (
            maybe_auto_scaffold,
            scaffold_sdk_missing_question,
        )

        scaffold_result = maybe_auto_scaffold(find_task_by_id(task_id) or active_task)
        sdk_question = scaffold_sdk_missing_question(scaffold_result)
        if sdk_question:
            live = find_task_by_id(task_id) or active_task
            if isinstance(live, dict):
                live.pop("structureScaffoldAttempted", None)
            parked = _try_move_to_needs_user(
                task_id, live, sdk_question, kind="stuck_loop"
            )
            if parked or (get_task_lane(task_id) or "") == "Needs User":
                result = sdk_question
                _ensure_dev_step_trace(task_id, title, lane_before)
                state.LAST_AGENT_STEP_RESULT = result
                with state.STATE_LOCK:
                    _record_last_step_outcome(
                        task_id, lane_before, "Developer", agent_result=result
                    )
                _finalize_dev_step_diagnostics_if_auto_sprint(task_id, lane_before)
                return
    except Exception:
        pass
    _ensure_dev_step_trace(task_id, title, lane_before)
    try:
        from backend.services.focus_slice import ensure_focus_initialized, focus_log_label

        ensure_focus_initialized(active_task)
        fresh_focus = find_task_by_id(task_id) or active_task
        if fresh_focus:
            ensure_focus_initialized(fresh_focus)
            fl = focus_log_label(fresh_focus)
            if fl:
                add_system_log("Developer", "info", fl)
    except Exception:
        pass
    try:
        model_note = f" [{model_in_use}]" if model_in_use else ""
        add_system_log(
            "Developer",
            "info",
            f"Implementing '{active_task['title']}'…{model_note}",
        )
        target = _dev_complete_lane()
        lint_cmd = derive_project_lint_command()
        lint_hint = f" (e.g. '{lint_cmd}')" if lint_cmd else ""
        max_in_card = int(get_workflow_settings().get("maxInCardLintFixes", 5))
        from backend.services.prompt_defaults import get_effective_step_instructions

        instructions = get_effective_step_instructions(
            "Developer",
            get_workflow_settings(),
            {
                "lint_hint": lint_hint,
                "max_in_card_lint": max_in_card,
                "target_lane": target,
                "autonomous_suffix": _autonomous_instruction_suffix(),
            },
        )
        from backend.services.workflow_settings import get_execution_profile

        if get_execution_profile() == "implementer":
            instructions = (
                instructions
                + "\nYou are a single implementer (Cursor-like). Do not move to Needs PO "
                "and do not ask the user. Read the named file, apply_patch, run the project "
                "lint/test command, and repeat until green or the step budget ends."
            )
        # Prefer prefetched context from parallel independent-card pipeline when present.
        prompt = None
        fresh_for_cache = find_task_by_id(task_id) or active_task
        cached = None
        if isinstance(fresh_for_cache, dict):
            cached = fresh_for_cache.pop("_cachedDevSprintPrompt", None)
        if isinstance(cached, str) and cached.strip():
            prompt = cached
            add_system_log("Developer", "info", f"{task_id}: using prefetched sprint context")
        if not prompt:
            prompt = _inject_sprint_context(active_task, brief, "Developer", instructions)
        try:
            from backend.services.workflow_settings import get_execution_profile
            from backend.workspace.files import seed_focus_preloaded_reads

            if get_execution_profile() == "implementer":
                seeded = seed_focus_preloaded_reads(
                    find_task_by_id(task_id) or active_task,
                    max_files=2,
                )
                if seeded:
                    add_system_log(
                        "Developer",
                        "info",
                        f"Pre-seeded read_file for: {', '.join(seeded)}",
                    )
        except Exception:
            pass
        try:
            from backend.services.card_ledger import run_dev_ideation, seed_ledger_from_task

            seed_ledger_from_task(find_task_by_id(task_id) or active_task)
            run_dev_ideation(agent_dev, find_task_by_id(task_id) or active_task)
        except Exception:
            pass
        from backend.services.fix_verify_loop import run_fix_verify_loop

        result = run_fix_verify_loop(
            agent_dev,
            active_task,
            prompt,
            max_iterations=_llm_iterations(find_task_by_id(task_id) or active_task),
        )
        try:
            from backend.services.system_auto_verify import maybe_run_system_auto_verify

            maybe_run_system_auto_verify(task_id, find_task_by_id(task_id) or active_task)
        except Exception:
            pass
        # Auto-extend outside STATE_LOCK (extend runs another LLM loop).
        try:
            fresh_pre = find_task_by_id(task_id) or active_task
            result = _maybe_auto_extend_dev_step(task_id, fresh_pre, result)
        except Exception:
            pass
        state.LAST_AGENT_STEP_RESULT = result

        with state.STATE_LOCK:
            task = find_task_by_id(task_id)
            if not task:
                return
            if _dev_step_read_only_no_edits(task, lane_before, step_started):
                state.DEV_STEP_READ_ONLY_NO_EDITS = True
                apply_read_only_no_edits_outcome(task)
                add_system_log(
                    "Developer",
                    "warning",
                    f"'{task.get('title', task_id)}': dev step read files but made no edits — "
                    "staying In Progress for a Forced Patch turn",
                )
            if _dev_step_repeated_command_no_progress(task, lane_before, step_started):
                state.DEV_STEP_COMMAND_REPEAT_NO_PROGRESS = True
                add_system_log(
                    "Developer",
                    "warning",
                    f"'{task.get('title', task_id)}': repeated identical run_command without progress — staying In Progress",
                )
            if result == "SIMULATION_FALLBACK":
                from backend.services.simulation_gate import (
                    apply_dev_offline_if_file_exists,
                    build_proposal,
                    dev_simulation_summary,
                    mark_step_outcome_simulation_pending,
                    preview_sprint_dev,
                    try_defer_simulation,
                )

                # Do not promote incomplete work to QA when Ollama died mid-step.
                if _dev_unhealthy_exit_blocks_advance("ollama_fallback"):
                    add_system_log(
                        "Developer",
                        "warning",
                        f"{task_id}: Ollama unavailable — staying In Progress "
                        "(unhealthy exit gate; set forceCompleteOnUnhealthyExit to override)",
                    )
                    preview = preview_sprint_dev(task)
                    prop = build_proposal(
                        kind="sprint_dev",
                        task_id=task_id,
                        agent="Developer",
                        title=str(task.get("title") or task_id),
                        summary=dev_simulation_summary(task),
                        default_preview=preview,
                        source="sprint_dev",
                    )
                    if try_defer_simulation(prop):
                        mark_step_outcome_simulation_pending(task_id, "Developer", lane_before)
                elif apply_dev_offline_if_file_exists(task, task_id=task_id, lane_before=lane_before):
                    result = "Used existing workspace file (Ollama offline)"
                else:
                    preview = preview_sprint_dev(task)
                    prop = build_proposal(
                        kind="sprint_dev",
                        task_id=task_id,
                        agent="Developer",
                        title=str(task.get("title") or task_id),
                        summary=dev_simulation_summary(task),
                        default_preview=preview,
                        source="sprint_dev",
                    )
                    if try_defer_simulation(prop):
                        mark_step_outcome_simulation_pending(task_id, "Developer", lane_before)
                    else:
                        _simulate_dev_work(task)
            else:
                record_task_decision(task_id, "Developer", "work", result[:500], result)
                if _task_in_lane(task_id, "In Progress"):
                    if dev_clarification_from_result(result):
                        if not _escalate_po_limit(task):
                            increment_po_round_trips(task_id)
                            move_board_stage(task_id, "Needs PO")
                            publish_activity(
                                task_id,
                                "dev_escalation",
                                "Developer needs clarification — routed to PO",
                                role="assistant",
                                agent="Developer",
                                lane="Needs PO",
                            )
                    elif _dev_needs_user(result):
                        if _needs_user_cap_reached():
                            add_system_log(
                                "Developer",
                                "warning",
                                f"{task_id}: autonomous cap — staying In Progress instead of Needs User",
                            )
                        elif _try_move_to_needs_user(
                            task_id, task, result[:500], kind="dev_escalation"
                        ):
                            pass
                        else:
                            add_system_log(
                                "Developer",
                                "warning",
                                f"{task_id}: Needs User escalation blocked — continuing In Progress",
                            )
                    elif _dev_needs_po(result, task):
                        if not _escalate_po_limit(task):
                            increment_po_round_trips(task_id)
                            move_board_stage(task_id, "Needs PO")
                            publish_activity(
                                task_id,
                                "dev_escalation",
                                "Developer escalated to PO for clarification",
                                role="assistant",
                                agent="Developer",
                                lane="Needs PO",
                            )
                    else:
                        fresh = find_task_by_id(task_id)
                        exit_reason = _provisional_dev_exit_reason(result, lane_before=lane_before)
                        if fresh and _dev_unhealthy_exit_blocks_advance(exit_reason):
                            add_system_log(
                                "Developer",
                                "warning",
                                f"{task_id}: blocked lane advance — unhealthy exit "
                                f"'{exit_reason}' (staying In Progress)",
                            )
                        elif fresh and _task_has_write_files(fresh):
                            blocked, reason = dev_gate_blocks_advance(fresh)
                            if blocked:
                                add_system_log("Developer", "warning", f"{task_id}: {reason}")
                            else:
                                from backend.services.focus_slice import (
                                    focus_advance_after_step,
                                    should_block_lane_advance_for_focus,
                                )
                                from backend.services.project_service import save_current_project_state

                                if should_block_lane_advance_for_focus(fresh):
                                    focus_advance_after_step(fresh, result)
                                    save_current_project_state(project_id=state.CURRENT_PROJECT_ID)
                                    add_system_log(
                                        "Developer",
                                        "info",
                                        f"{task_id}: focus slice advanced — staying In Progress for next criterion",
                                    )
                                else:
                                    clear_qa_failure(task_id)
                                    move_board_stage(task_id, target)
                        elif fresh:
                            add_system_log(
                                "Developer",
                                "warning",
                                f"'{fresh.get('title', task_id)}' finished with no files written — "
                                "staying In Progress",
                            )
            # Hybrid lint fan-out when fix-verify is off or leftovers remain after the step.
            if (
                result != "SIMULATION_FALLBACK"
                and not state.DEV_STEP_READ_ONLY_NO_EDITS
                and not state.DEV_STEP_COMMAND_REPEAT_NO_PROGRESS
                and _task_in_lane(task_id, "In Progress")
            ):
                _maybe_advance_dev_after_lint_write(
                    task_id, find_task_by_id(task_id) or task, lane_before
                )
                if _task_in_lane(task_id, "In Progress"):
                    _maybe_advance_dev_after_verify(
                        task_id, find_task_by_id(task_id) or task, lane_before
                    )
            fresh_for_lint = find_task_by_id(task_id) or task
            diags = fresh_for_lint.get("lastCommandDiagnostics") or []
            if isinstance(diags, list) and diags:
                try:
                    from backend.services.lint_fanout import maybe_fanout_lint_diagnostics

                    maybe_fanout_lint_diagnostics(fresh_for_lint, diags, step_marker=step_started)
                except Exception:
                    pass
            # Record outcome first so exitReason is available, then arm backup.
            read_only_flag = bool(state.DEV_STEP_READ_ONLY_NO_EDITS)
            _log_sprint_step_outcome(
                "Developer", task_id, task.get("title", task_id), lane_before, result
            )
            try:
                _record_last_step_outcome(
                    task_id, lane_before, "Developer", agent_result=result
                )
            except Exception:
                pass
            try:
                from backend.services.sprint_speed_gates import last_step_exit_reason

                fresh = find_task_by_id(task_id)
                if fresh and last_step_exit_reason(fresh) == "duplicate_tool":
                    apply_read_only_no_edits_outcome(fresh)
                    add_system_log(
                        "Developer",
                        "warning",
                        f"'{fresh.get('title', task_id)}': duplicate tool loop — "
                        "staying In Progress for a Forced Patch turn",
                    )
            except Exception:
                pass
            try:
                from backend.services.backup_model import (
                    arm_backup_for_agent,
                    should_arm_from_exit_reason,
                    should_force_arm_from_exit_reason,
                )
                from backend.services.step_diagnostics import derive_exit_reason, get_active_trace

                exit_reason = None
                if read_only_flag:
                    exit_reason = "read_only_no_edits"
                else:
                    outcome = state.LAST_STEP_OUTCOME or {}
                    if isinstance(outcome, dict):
                        exit_reason = outcome.get("exitReason") or outcome.get("stopReason")
                    if not exit_reason:
                        trace = get_active_trace()
                        tools = set(trace.tools_used) if trace else set()
                        exit_reason = derive_exit_reason(
                            agent_result=result,
                            tools_used=tools,
                            lane_before=lane_before,
                            lane_after=get_task_lane(task_id) or lane_before,
                        )
                if should_arm_from_exit_reason(exit_reason):
                    arm_backup_for_agent(
                        "dev",
                        find_task_by_id(task_id) or task,
                        reason=str(exit_reason),
                        force=should_force_arm_from_exit_reason(exit_reason),
                    )
            except Exception:
                pass
            _audit_dev_files_written(find_task_by_id(task_id) or task, lane_before, task_id)
            _audit_dev_verification(
                find_task_by_id(task_id) or task, lane_before, task_id, step_started
            )
            _check_stuck_and_escalate(task_id, lane_before, agent_key="dev")
    except Exception:
        _ensure_interrupted_step_recorded(task_id, lane_before, "Developer")
        raise
    finally:
        _finalize_dev_step_diagnostics_if_auto_sprint(task_id, lane_before)


def _run_code_review_step(active_task: Dict[str, Any], brief: str) -> None:
    task_id = active_task["id"]
    lane_before = get_task_lane(task_id) or "Code Review"
    title = str(active_task.get("title", task_id))
    _mark_sprint_step_start()
    set_active_sprint_context(task_id, "Code Reviewer")
    _ensure_step_trace(task_id, title, "Code Reviewer", lane_before)
    result = ""
    try:
        try:
            from backend.services.backup_model import apply_model_for_step

            apply_model_for_step(agent_cr, "cr", find_task_by_id(task_id) or active_task)
        except Exception:
            pass
        add_system_log("Code Reviewer", "info", f"Reviewing '{active_task['title']}'…")
        from backend.services.prompt_defaults import get_effective_step_instructions

        instructions = get_effective_step_instructions("Code Reviewer", get_workflow_settings(), {})
        prompt = _inject_sprint_context(active_task, brief, "Code Reviewer", instructions)
        result = agent_cr.execute_step(prompt, max_iterations=_llm_iterations())

        with state.STATE_LOCK:
            if not find_task_by_id(task_id):
                return
            if result == "SIMULATION_FALLBACK":
                task = find_task_by_id(task_id)
                if task:
                    from backend.services.simulation_gate import (
                        build_proposal,
                        mark_step_outcome_simulation_pending,
                        preview_sprint_cr,
                        try_defer_simulation,
                    )

                    preview = preview_sprint_cr()
                    prop = build_proposal(
                        kind="sprint_cr",
                        task_id=task_id,
                        agent="Code Reviewer",
                        title=str(task.get("title") or task_id),
                        summary="Offline code review: random pass to QA or fail to In Progress",
                        default_preview=preview,
                        source="sprint_cr",
                    )
                    if try_defer_simulation(prop):
                        mark_step_outcome_simulation_pending(task_id, "Code Reviewer", lane_before)
                    else:
                        _simulate_code_review(task)
            else:
                record_task_decision(task_id, "Code Reviewer", "review", result[:500], result)
                if _task_in_lane(task_id, "Code Review"):
                    move_board_stage(task_id, "QA")
            task = find_task_by_id(task_id)
            if task:
                _log_sprint_step_outcome(
                    "Code Reviewer", task_id, task.get("title", task_id), lane_before, result
                )
            _check_stuck_and_escalate(task_id, lane_before, agent_key="cr")
    finally:
        _finalize_role_step_diagnostics(
            task_id, lane_before, "Code Reviewer", agent_result=result or None
        )


def _run_qa_step(active_task: Dict[str, Any], brief: str) -> None:
    task_id = active_task["id"]
    lane_before = get_task_lane(task_id) or "QA"
    title = str(active_task.get("title", task_id))
    step_started = _mark_sprint_step_start()
    set_active_sprint_context(task_id, "QA Tester")
    _ensure_step_trace(task_id, title, "QA Tester", lane_before)
    result = ""
    try:
        try:
            from backend.services.backup_model import apply_model_for_step

            apply_model_for_step(agent_qa, "qa", find_task_by_id(task_id) or active_task)
        except Exception:
            pass
        add_system_log("QA Tester", "info", f"Validating '{active_task['title']}'…")

        playbook = _run_qa_test_playbook(task_id)
        with state.STATE_LOCK:
            task_for_evidence = find_task_by_id(task_id)
            if task_for_evidence:
                normalize_task(task_for_evidence)
                task_for_evidence["qaEvidence"] = {
                    "playbookRun": playbook["run"],
                    "commands": playbook["commands"],
                    "passed": playbook["passed"],
                }

        ac = active_task.get("acceptanceCriteria") or []
        ac_block = "\n".join(f"- {c}" for c in ac) if ac else "(see description)"
        from backend.services.prompt_defaults import get_effective_step_instructions

        instructions = get_effective_step_instructions(
            "QA Tester",
            get_workflow_settings(),
            {
                "ac_block": ac_block,
                "dod_block": build_dod_block(),
                "playbook_block": _format_playbook_block(playbook),
            },
        )
        prompt = _inject_sprint_context(active_task, brief, "QA Tester", instructions)
        result = agent_qa.execute_step(prompt, max_iterations=_llm_iterations())

        with state.STATE_LOCK:
            task = find_task_by_id(task_id)
            if not task:
                return
            normalize_task(task)
            passed, fail_reason = (False, "") if result == "SIMULATION_FALLBACK" else _qa_step_passed(
                task, result, playbook, step_started
            )
            task["qaEvidence"] = {
                "playbookRun": playbook["run"],
                "commands": playbook["commands"],
                "passed": passed,
            }
            from backend.services.card_delivery import update_ac_verification_from_qa

            update_ac_verification_from_qa(
                task,
                passed=passed,
                commands=list(playbook.get("commands") or []),
                failure_reason=fail_reason if not passed else "",
            )
            if result == "SIMULATION_FALLBACK":
                from backend.services.simulation_gate import (
                    build_proposal,
                    mark_step_outcome_simulation_pending,
                    preview_sprint_qa,
                    try_defer_simulation,
                )

                preview = preview_sprint_qa()
                prop = build_proposal(
                    kind="sprint_qa",
                    task_id=task_id,
                    agent="QA Tester",
                    title=str(task.get("title") or task_id),
                    summary="Offline QA: random pass to Done or fail to In Progress",
                    default_preview=preview,
                    source="sprint_qa",
                )
                if try_defer_simulation(prop):
                    mark_step_outcome_simulation_pending(task_id, "QA Tester", lane_before)
                else:
                    _simulate_qa(task)
            else:
                record_task_decision(task_id, "QA Tester", "qa", result[:500], result)
                if _task_in_lane(task_id, "QA"):
                    if not passed:
                        reason = fail_reason if fail_reason else result[:500]
                        set_qa_failure(task_id, reason, result)
                        record_task_decision(task_id, "QA Tester", "qa_fail", reason, result)
                        move_board_stage(task_id, "In Progress")
                    else:
                        move_board_stage(task_id, "Done")
                        _commit_on_done(task)
            _log_sprint_step_outcome("QA Tester", task_id, task.get("title", task_id), lane_before, result)
            _check_stuck_and_escalate(task_id, lane_before, agent_key="qa")
    finally:
        _finalize_role_step_diagnostics(
            task_id, lane_before, "QA Tester", agent_result=result or None
        )


def _sprint_lanes_active() -> List[str]:
    ws = get_workflow_settings()
    lanes = ["Needs PO", "In Progress"]
    prioritize_impl = ws.get("prioritizeImplementationOverRefinement", True)
    if prioritize_impl and ws.get("requireBacklogRefinement"):
        lanes.append("Backlog")
        lanes.append("Refinement")
    elif ws.get("requireBacklogRefinement"):
        lanes.append("Refinement")
        lanes.append("Backlog")
    else:
        lanes.append("Backlog")
    if ws.get("requireCodeReview"):
        lanes.insert(lanes.index("Backlog") + 1, "Code Review")
    lanes.append("QA")
    return lanes


def _try_refinement_handler() -> tuple[Optional[str], Optional[Dict[str, Any]]]:
    """Return (handler, active_task) for Refinement lane work, or (None, None)."""
    ws = get_workflow_settings()
    if not ws.get("requireBacklogRefinement") or not state.SHARED_BOARD.get("Refinement"):
        return None, None
    spike = next_spike_task()
    if spike:
        return "spike_dev", dict(spike)
    task = next_refinement_task()
    if task:
        status = str(task.get("refinementStatus") or "pending")
        handler = "refinement_po" if status == "dev_reviewed" else "refinement_dev"
        return handler, dict(task)
    # Non-empty Refinement but nothing claimable — do not return "idle" (would starve other lanes).
    return None, None


def _escalate_dependency_deadlock(task: Dict[str, Any], issues: Dict[str, Any]) -> None:
    """Move a permanently blocked card to Needs User with a clear question."""
    tid = str(task.get("id") or "")
    if not tid:
        return
    if issues.get("cycle"):
        path = " → ".join(str(p) for p in (issues.get("cyclePath") or []))
        reason = (
            f"Which blockedBy link should we remove to break the cycle"
            f" ({path or tid})?"
        )
    else:
        missing = issues.get("missing") or []
        reason = (
            f"These blockedBy ids are missing: {', '.join(missing) or tid}. "
            "Remove the invalid links, or create those cards?"
        )
    moved = _try_move_to_needs_user(tid, task, reason, kind="stuck_loop")
    if not moved:
        from backend.services.needs_user_guard import apply_needs_user_brief, build_needs_user_brief

        apply_needs_user_brief(task, build_needs_user_brief(task, kind="stuck_loop", raw_msg=reason))
        move_board_stage(tid, "Needs User")
    record_task_decision(tid, "System", "escalation", reason[:300])
    add_system_log("System", "warning", f"{tid}: {reason}")
    if not moved:
        publish_activity(
            tid,
            "dependency_deadlock",
            reason,
            role="system",
            agent="System",
            lane="Needs User",
        )


def _try_claim_dependency_unblocker() -> tuple[Optional[str], Optional[Dict[str, Any]]]:
    """Prefer working a dependency of a blocked parent (unblockers first)."""
    sort_backlog()
    blocked_parents = []
    for lane in ("Blocked", "Backlog", "Refinement", "Pending Approval"):
        blocked_parents.extend(
            t
            for t in state.SHARED_BOARD.get(lane, [])
            if isinstance(t, dict) and not task_dependencies_met(t)
        )
    for parent in blocked_parents:
        normalize_task(parent)
        issues = detect_blocked_by_issues(parent)
        if issues.get("cycle") or (
            issues.get("missing") and not any(
                find_task_by_id(str(d)) for d in (parent.get("blockedBy") or [])
            )
        ):
            # All deps missing, or a cycle — escalate instead of waiting forever.
            if issues.get("cycle") or issues.get("missing"):
                _escalate_dependency_deadlock(parent, issues)
            continue

        for dep in parent.get("blockedBy") or []:
            dep_id = str(dep)
            if not dep_id or is_task_done(dep_id):
                continue
            dep_task = find_task_by_id(dep_id)
            if not dep_task:
                continue
            lane = get_task_lane(dep_id) or ""
            if lane == "Backlog" and is_backlog_claimable(dep_task):
                move_board_stage(dep_id, "In Progress")
                record_task_decision(
                    dep_id,
                    "Developer",
                    "claim",
                    f"Claimed as dependency unblocker for {parent.get('id')}",
                )
                claimed = find_task_by_id(dep_id) or dep_task
                return "dev", dict(claimed)
            if lane == "Refinement" and is_refinement_claimable(dep_task):
                status = str(dep_task.get("refinementStatus") or "pending")
                handler = "refinement_po" if status == "dev_reviewed" else "refinement_dev"
                return handler, dict(dep_task)
            if lane == "Refinement" and dep_task.get("workType") == "spike":
                spike = next_spike_task()
                if spike and str(spike.get("id")) == dep_id:
                    return "spike_dev", dict(spike)
    return None, None


def _try_backlog_handler() -> tuple[Optional[str], Optional[Dict[str, Any]]]:
    """Return (handler, active_task) for Backlog lane work, or (None, None).

    Never returns handler \"blocked\" — that used to short-circuit other lanes.
    """
    if state.SHARED_BOARD.get("Backlog"):
        task = next_claimable_backlog_task()
        if task:
            move_board_stage(task["id"], "In Progress")
            record_task_decision(task["id"], "Developer", "claim", "Claimed from Backlog")
            claimed = find_task_by_id(task["id"]) or task
            return "dev", dict(claimed)
        from backend.services.task_spec_validation import dev_claim_blocked

        for blocked_task in list(state.SHARED_BOARD.get("Backlog") or []):
            if not isinstance(blocked_task, dict) or not task_dependencies_met(blocked_task):
                continue
            reason = dev_claim_blocked(blocked_task, get_workflow_settings())
            if not reason:
                continue
            move_board_stage(str(blocked_task["id"]), "In Progress")
            lane_now = get_task_lane(str(blocked_task["id"]))
            if lane_now == "Needs PO":
                routed = find_task_by_id(str(blocked_task["id"])) or blocked_task
                return "po", dict(routed)
            # A deterministic AC split may have retired this parent and exposed a child.
            child = next_claimable_backlog_task()
            if child:
                move_board_stage(str(child["id"]), "In Progress")
                claimed = find_task_by_id(str(child["id"])) or child
                return "dev", dict(claimed)
        po_plan = next_po_planning_backlog_task()
        if po_plan:
            move_board_stage(po_plan["id"], "Needs PO")
            record_task_decision(
                po_plan["id"],
                "Product Owner",
                "escalation",
                "Planning card routed to PO",
            )
            return "po", dict(find_task_by_id(po_plan["id"]) or po_plan)

    unblocker = _try_claim_dependency_unblocker()
    if unblocker[0] is not None:
        return unblocker

    # Escalate any remaining cycle / all-missing parents.
    for lane in ("Blocked", "Backlog"):
        for parent in list(state.SHARED_BOARD.get(lane, [])):
            if task_dependencies_met(parent):
                continue
            issues = detect_blocked_by_issues(parent)
            if issues.get("cycle") or (
                issues.get("missing")
                and not any(find_task_by_id(str(d)) for d in (parent.get("blockedBy") or []))
            ):
                _escalate_dependency_deadlock(parent, issues)

    return None, None


def _log_idle_dependency_status() -> None:
    """Richer diagnostic when sprint is idle but cards wait on dependencies."""
    blocked_lane = [
        t for t in state.SHARED_BOARD.get("Blocked", []) if isinstance(t, dict)
    ]
    if blocked_lane:
        add_system_log(
            "System",
            "info",
            f"{len(blocked_lane)} card(s) in Blocked waiting on deps.",
        )
        t = blocked_lane[0]
        normalize_task(t)
        status = format_dependency_block_status(t)
        add_system_log(
            "System",
            "info",
            f"Blocked — waiting on dependencies for {t.get('id')}: {status}",
        )
        return
    blocked = [t for t in state.SHARED_BOARD.get("Backlog", []) if not task_dependencies_met(t)]
    if not blocked:
        return
    t = blocked[0]
    normalize_task(t)
    status = format_dependency_block_status(t)
    add_system_log(
        "System",
        "info",
        f"Backlog blocked — waiting on dependencies for {t.get('id')}: {status}",
    )


def _first_runnable_needs_po(board: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """Oldest Needs PO card that is not circuit-latched / auto-skipped."""
    from backend.services.sprint_speed_gates import needs_po_should_skip_auto

    board = board if board is not None else state.SHARED_BOARD
    for task in board.get("Needs PO") or []:
        if not isinstance(task, dict):
            continue
        if needs_po_should_skip_auto(task):
            continue
        return task
    return None


def _in_progress_dev_runnable(board: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    from backend.services.sprint_speed_gates import no_write_stall_should_park, empty_gen_should_skip

    board = board if board is not None else state.SHARED_BOARD
    return [
        task
        for task in board.get("In Progress") or []
        if isinstance(task, dict)
        and not task.get("phaseCycleCapReached")
        and not task.get("pendingSplit")
        and not no_write_stall_should_park(task)
        and not empty_gen_should_skip(task)
    ]


def _in_progress_pending_recovery(board: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    from backend.services.sprint_speed_gates import no_write_stall_should_park

    board = board if board is not None else state.SHARED_BOARD
    runnable_ids = {
        str(t.get("id") or "")
        for t in _in_progress_dev_runnable(board)
        if isinstance(t, dict)
    }
    return [
        task
        for task in board.get("In Progress") or []
        if isinstance(task, dict)
        and not task.get("latchedRecoveryAttempted")
        and (
            task.get("phaseCycleCapReached")
            or no_write_stall_should_park(task)
        )
        and not (
            str(task.get("id") or "") in runnable_ids
            and task.get("forcePatchNextDevStep")
            and is_lint_wall_card(task)
        )
    ]


def _in_progress_exhausted_latched(board: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """Latched after one recovery — park once more unless a prior park already failed."""
    board = board if board is not None else state.SHARED_BOARD
    return [
        task
        for task in board.get("In Progress") or []
        if isinstance(task, dict)
        and task.get("phaseCycleCapReached")
        and task.get("latchedRecoveryAttempted")
        and not task.get("parkFailed")
    ]


def _needs_po_pending_park(board: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """Latched Needs PO cards that have not been parked yet."""
    board = board if board is not None else state.SHARED_BOARD
    return [
        task
        for task in board.get("Needs PO") or []
        if isinstance(task, dict)
        and task.get("phaseCycleCapReached")
        and not task.get("latchedRecoveryAttempted")
    ]


def _select_downstream_sprint_handler() -> tuple[Optional[str], Optional[Dict[str, Any]]]:
    """Backlog / refinement / CR / QA / idle. Caller holds STATE_LOCK."""
    handler: Optional[str] = None
    active_task: Optional[Dict[str, Any]] = None
    ws = get_workflow_settings()
    prioritize_impl = ws.get("prioritizeImplementationOverRefinement", True)
    if prioritize_impl and ws.get("requireBacklogRefinement"):
        backlog_handler, backlog_task = _try_backlog_handler()
        if backlog_handler is not None:
            handler, active_task = backlog_handler, backlog_task
        if handler is None:
            refine_handler, refine_task = _try_refinement_handler()
            if refine_handler is not None:
                handler, active_task = refine_handler, refine_task
    else:
        refine_handler, refine_task = _try_refinement_handler()
        if refine_handler is not None:
            handler, active_task = refine_handler, refine_task
        if handler is None:
            backlog_handler, backlog_task = _try_backlog_handler()
            if backlog_handler is not None:
                handler, active_task = backlog_handler, backlog_task
    if handler is None and ws.get("requireCodeReview") and state.SHARED_BOARD.get("Code Review"):
        active_task = dict(state.SHARED_BOARD["Code Review"][0])
        handler = "cr"
    elif handler is None and state.SHARED_BOARD.get("QA"):
        qa_candidate = dict(state.SHARED_BOARD["QA"][0])
        normalize_task(qa_candidate)
        if not qa_candidate.get("requiresQa", True):
            move_board_stage(qa_candidate["id"], "Done")
            record_task_decision(
                qa_candidate["id"],
                "System",
                "move",
                "Skipped QA (requiresQa=false)",
            )
            handler = "idle"
        else:
            active_task = qa_candidate
            handler = "qa"
    elif handler is None:
        handler = "idle"
    return handler, active_task


def has_sprint_work() -> bool:
    """True when auto-sprint has actionable (not merely blocked) work."""
    board = state.SHARED_BOARD
    if (
        _first_runnable_needs_po(board)
        or _needs_po_pending_park(board)
        or _in_progress_dev_runnable(board)
        or _in_progress_pending_recovery(board)
    ):
        return True
    ws = get_workflow_settings()
    if ws.get("requireCodeReview") and board.get("Code Review"):
        return True
    if board.get("QA"):
        return True
    if next_claimable_backlog_task() or next_po_planning_backlog_task():
        return True
    from backend.services.task_spec_validation import dev_claim_blocked

    if any(
        isinstance(task, dict)
        and task_dependencies_met(task)
        and dev_claim_blocked(task, ws)
        for task in board.get("Backlog", [])
    ):
        return True
    if ws.get("requireBacklogRefinement"):
        if next_spike_task() or next_refinement_task():
            return True
    # A blocked parent with a claimable dependency is still actionable.
    sort_backlog()
    for lane in ("Blocked", "Backlog"):
        for parent in board.get(lane, []):
            if task_dependencies_met(parent):
                continue
            for dep in parent.get("blockedBy") or []:
                dep_id = str(dep)
                dep_task = find_task_by_id(dep_id)
                if not dep_task:
                    continue
                dep_lane = get_task_lane(dep_id) or ""
                if dep_lane == "Backlog" and is_backlog_claimable(dep_task):
                    return True
                if dep_lane == "Refinement" and (
                    is_refinement_claimable(dep_task) or dep_task.get("workType") == "spike"
                ):
                    return True
    return False


def list_independent_in_progress_cards(limit: int = 2) -> List[Dict[str, Any]]:
    """In Progress cards with no blockedBy — candidates for parallel context pipeline."""
    limit = max(1, int(limit or 1))
    out: List[Dict[str, Any]] = []
    with state.STATE_LOCK:
        for task in list(state.SHARED_BOARD.get("In Progress") or []):
            if not isinstance(task, dict):
                continue
            if task.get("phaseCycleCapReached"):
                continue
            blocked = task.get("blockedBy") or []
            if blocked:
                continue
            out.append(dict(task))
            if len(out) >= limit:
                break
    return out


def _prefetch_dev_sprint_context(active_task: Dict[str, Any], brief: str) -> None:
    """Build Dev prompt off the critical path and stash on the live board task."""
    task_id = str(active_task.get("id") or "")
    if not task_id:
        return
    try:
        lint_cmd = derive_project_lint_command()
        lint_hint = f" (e.g. '{lint_cmd}')" if lint_cmd else ""
        max_in_card = int(get_workflow_settings().get("maxInCardLintFixes", 5))
        from backend.services.prompt_defaults import get_effective_step_instructions

        instructions = get_effective_step_instructions(
            "Developer",
            get_workflow_settings(),
            {
                "lint_hint": lint_hint,
                "max_in_card_lint": max_in_card,
                "target_lane": _dev_complete_lane(),
                "autonomous_suffix": _autonomous_instruction_suffix(),
            },
        )
        prompt = _inject_sprint_context(active_task, brief, "Developer", instructions)
        with state.STATE_LOCK:
            live = find_task_by_id(task_id)
            if live:
                live["_cachedDevSprintPrompt"] = prompt
        add_system_log(
            "Developer",
            "info",
            f"Prefetched sprint context for '{active_task.get('title', task_id)}'",
        )
    except Exception as exc:
        add_system_log(
            "Developer",
            "warning",
            f"Prefetch context failed for {task_id}: {type(exc).__name__}",
        )


def _run_parallel_independent_dev_batch(brief: str, ollama_url: str) -> int:
    """
    Pipeline independent In Progress cards: prefetch next card's context while
    the current card runs. Returns number of Dev steps executed (0 if not applicable).
    """
    ws = get_workflow_settings()
    if not ws.get("enableParallelIndependentCards"):
        return 0
    # Prefer runnable Needs PO / other handlers first — latched Needs PO must not starve Dev.
    with state.STATE_LOCK:
        if _first_runnable_needs_po():
            return 0
        if ws.get("pauseSprintOnNeedsUser") and state.SHARED_BOARD.get("Needs User"):
            return 0
    limit = max(2, int(ws.get("maxParallelDevCards") or 2))
    cards = list_independent_in_progress_cards(limit=limit)
    if len(cards) < 2:
        return 0

    from concurrent.futures import ThreadPoolExecutor

    agent_dev.ollama_url = ollama_url
    ran = 0
    with ThreadPoolExecutor(max_workers=1) as pool:
        for idx, card in enumerate(cards):
            if state.SPRINT_CANCEL:
                break
            next_card = cards[idx + 1] if idx + 1 < len(cards) else None
            fut = None
            if next_card is not None:
                fut = pool.submit(_prefetch_dev_sprint_context, next_card, brief)
            _run_developer_step(card, brief)
            ran += 1
            if fut is not None:
                try:
                    fut.result()
                except Exception:
                    pass
    add_system_log(
        "System",
        "info",
        f"Parallel independent card pipeline ran {ran} Dev step(s)",
    )
    return ran


def _handle_lint_stuck(task_id: str, task: Dict[str, Any], reason: str) -> None:
    """Route lint wall cards through shared-file blocker; fall back to In Progress Dev retry."""
    from backend.services.file_blocker import orchestrate_from_task

    live = find_task_by_id(task_id) or task
    if not is_lint_wall_card(live):
        return
    result = orchestrate_from_task(live, reason=reason)
    blocked = result.get("blocked") or []
    if task_id in blocked:
        return
    _keep_lint_card_for_developer(live)


def _keep_lint_card_for_developer(task: Dict[str, Any]) -> bool:
    """Keep analyzer/lint walls on In Progress for a Forced Patch Dev step.

    Unlatches the visit cap once. After that, skip Auto Sprint without Needs User.
    Returns True when Developer should run again.
    """
    from backend.services.sprint_speed_gates import reset_dev_cycle_latch

    task_id = str(task.get("id") or "")
    task["forcePatchNextDevStep"] = True
    task["consecutiveNoWriteStall"] = 0
    task.pop("lastNextWorkNoWrite", None)
    task.pop("parkFailed", None)
    task["latchedRecoveryAttempted"] = False
    task["poAutoSkip"] = False
    lane = get_task_lane(task_id) if task_id else ""
    if task_id and lane and lane != "In Progress":
        task["status"] = "In Progress"
        move_board_stage(task_id, "In Progress")
    if task.get("phaseCycleCapReached"):
        used = int(task.get("lintUnlatchCount") or 0)
        if used >= 1:
            ws = get_workflow_settings()
            max_stuck = int(ws.get("maxStuckSteps", 3) or 3)
            if _should_attempt_stuck_auto_split(task, ws):
                if _run_stuck_auto_split(task_id, task, max_stuck):
                    return False
            task["parkFailed"] = True
            task["poAutoSkip"] = True
            task["latchedRecoveryAttempted"] = True
            task["forcePatchNextDevStep"] = True
            add_system_log(
                "System",
                "warning",
                f"{task_id}: lint wall after one visit-cap unlatch — "
                "skipping Auto Sprint (not Needs User)",
            )
            return False
        task["lintUnlatchCount"] = used + 1
        reset_dev_cycle_latch(task)
        task["forcePatchNextDevStep"] = True
        add_system_log(
            "System",
            "info",
            f"{task_id}: lint/tool errors — staying In Progress; "
            f"unlatched visit cap ({task['lintUnlatchCount']}/1) for Forced Patch",
        )
        return True
    add_system_log(
        "System",
        "info",
        f"{task_id}: lint/tool errors — staying In Progress for Forced Patch",
    )
    return True


def _lint_recovery_should_record_outcome(task: Dict[str, Any]) -> bool:
    """True when System lint recovery should replace lastStepOutcome (latched/stall paths)."""
    from backend.services.sprint_speed_gates import no_write_stall_should_park

    if task.get("phaseCycleCapReached"):
        return True
    if no_write_stall_should_park(task):
        return True
    if task.get("latchedRecoveryAttempted"):
        return True
    return False


def _recover_latched_dev_card(
    active_task: Dict[str, Any],
    brief: str,
    *,
    quiet: bool = False,
) -> None:
    """Park a phase-cycle-capped card, or keep lint walls on Developer."""
    del brief
    task_id = str(active_task.get("id") or "")
    if not task_id:
        return
    lane_before = get_task_lane(task_id) or "In Progress"
    title = str(active_task.get("title") or task_id)
    park_msg = (
        "Phase cycle cap reached. Split the card or reset the Developer visit latch. "
        "Auto Sprint will not run Product Owner or Developer on this card."
    )
    with state.STATE_LOCK:
        task = find_task_by_id(task_id)
        if not task:
            return
        if is_lint_wall_card(task):
            _handle_lint_stuck(task_id, task, park_msg)
            lane_now = get_task_lane(task_id) or ""
            kept = lane_now == "In Progress" and not task.get("parkFailed")
            record_outcome = not quiet and _lint_recovery_should_record_outcome(task)
            if lane_now == "Done":
                if record_outcome:
                    result = "Visit-cap lint card auto-split; parent superseded."
                    _ensure_step_trace(task_id, title, "System", lane_before)
                    state.LAST_AGENT_STEP_RESULT = result
                    _record_last_step_outcome(task_id, lane_before, "System", agent_result=result)
                    _finalize_dev_step_diagnostics_if_auto_sprint(task_id, lane_before)
                return
            if record_outcome:
                result = (
                    "Lint/tool errors remain — staying In Progress so Developer can patch. "
                    "Not moving to Needs User."
                    if kept
                    else (
                        "Lint wall after visit-cap unlatch — staying In Progress "
                        "(not Needs User)."
                    )
                )
                _ensure_step_trace(task_id, title, "System", lane_before)
                state.LAST_AGENT_STEP_RESULT = result
                _record_last_step_outcome(task_id, lane_before, "System", agent_result=result)
                _finalize_dev_step_diagnostics_if_auto_sprint(task_id, lane_before)
            return
        ws = get_workflow_settings()
        max_stuck = int(ws.get("maxStuckSteps", 3) or 3)
        if _should_attempt_stuck_auto_split(task, ws):
            if _run_stuck_auto_split(task_id, task, max_stuck):
                result = "Visit-cap card auto-split; parent superseded."
                if not quiet:
                    _ensure_step_trace(task_id, title, "System", lane_before)
                    state.LAST_AGENT_STEP_RESULT = result
                    _record_last_step_outcome(task_id, lane_before, "System", agent_result=result)
                    _finalize_dev_step_diagnostics_if_auto_sprint(task_id, lane_before)
                return
            task["latchedRecoveryAttempted"] = True
            task["poAutoSkip"] = True
            task["parkFailed"] = True
            result = (
                "Stopped: phase cycle cap reached. Auto-split did not supersede the parent; "
                "staying In Progress (not Needs User)."
            )
            if not quiet:
                _ensure_step_trace(task_id, title, "System", lane_before)
                state.LAST_AGENT_STEP_RESULT = result
                _record_last_step_outcome(task_id, lane_before, "System", agent_result=result)
                _finalize_dev_step_diagnostics_if_auto_sprint(task_id, lane_before)
            return
        task["latchedRecoveryAttempted"] = True
        task["poAutoSkip"] = True
        result = (
            "Stopped: phase cycle cap reached. Developer execution is latched; "
            "split the card or reset the latch — Auto Sprint will not run PO or Dev."
        )
        if not quiet:
            _ensure_step_trace(task_id, title, "System", lane_before)
            state.LAST_AGENT_STEP_RESULT = result
            _record_last_step_outcome(task_id, lane_before, "System", agent_result=result)
        parked = _try_move_to_needs_user(task_id, task, park_msg, kind="phase_cycle_cap")
        if not parked:
            task["parkFailed"] = True
            add_system_log(
                "System",
                "warning",
                f"{task_id}: phase-cycle-cap park did not move to Needs User — "
                "skipping Auto Sprint PO/Dev on this card",
            )
        else:
            task.pop("parkFailed", None)
        if not quiet:
            _finalize_dev_step_diagnostics_if_auto_sprint(task_id, lane_before)


def _side_park_latched_needs_po(brief: str, exclude_id: str = "") -> None:
    """Park latched Needs PO cards that are not the active sprint handler."""
    pending: List[Dict[str, Any]] = []
    with state.STATE_LOCK:
        for task in _needs_po_pending_park():
            if str(task.get("id") or "") == exclude_id:
                continue
            pending.append(dict(task))
    for task in pending:
        _recover_latched_dev_card(task, brief, quiet=True)


def _select_sprint_step_handler() -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
    """Pick the next sprint handler. Prefer Dev over skippable Needs PO."""
    from backend.services.po_clarification import (
        should_move_off_needs_po_without_llm,
        task_has_ready_spec,
    )
    from backend.services.workflow_settings import get_execution_profile

    profile = get_execution_profile()
    needs_po_task = _first_runnable_needs_po()
    runnable = _in_progress_dev_runnable()
    pending_recovery = _in_progress_pending_recovery()

    def _idle_recovery() -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
        handler, active = _select_downstream_sprint_handler()
        if handler not in (None, "idle", "needs_user", "blocked"):
            return handler, active
        pending_npo = _needs_po_pending_park()
        if pending_npo:
            return "dev_recovery", dict(pending_npo[0])
        exhausted = _in_progress_exhausted_latched()
        if exhausted:
            return "dev_recovery", dict(exhausted[0])
        return handler, active

    if profile == "implementer":
        if needs_po_task and not task_has_ready_spec(needs_po_task):
            if runnable:
                return "dev", dict(runnable[0])
            return "po", dict(needs_po_task)
        if runnable and needs_po_task and task_has_ready_spec(needs_po_task):
            return "dev", dict(runnable[0])
        if runnable:
            return "dev", dict(runnable[0])
        if pending_recovery:
            return "dev_recovery", dict(pending_recovery[0])
        handler, active = _idle_recovery()
        if handler not in (None, "idle"):
            return handler, active
        if needs_po_task:
            return "po", dict(needs_po_task)
        return handler, active

    if needs_po_task:
        skippable = should_move_off_needs_po_without_llm(needs_po_task)
        if runnable and skippable:
            return "dev", dict(runnable[0])
        return "po", dict(needs_po_task)
    if get_workflow_settings().get("pauseSprintOnNeedsUser") and state.SHARED_BOARD.get(
        "Needs User"
    ):
        return "needs_user", None
    if runnable:
        return "dev", dict(runnable[0])
    if pending_recovery:
        return "dev_recovery", dict(pending_recovery[0])
    return _idle_recovery()


def run_sprint_step(brief: str, ollama_url: str) -> None:
    brief = resolve_brief_for_sprint(brief)
    agent_dev.ollama_url = ollama_url
    agent_po.ollama_url = ollama_url
    agent_qa.ollama_url = ollama_url
    agent_cr.ollama_url = ollama_url

    try:
        from backend.services.blocked_lane import sync_blocked_lane

        sync_blocked_lane(persist=True)
    except Exception:
        pass

    try:
        drain_pending_splits(ollama_url)
    except Exception:
        pass

    try:
        from backend.services.lint_fanout import retire_junk_lint_cards

        retire_junk_lint_cards()
    except Exception:
        pass

    try:
        from backend.services.needs_user_guard import reconcile_autonomous_needs_user_cards

        reconcile_autonomous_needs_user_cards()
    except Exception:
        pass

    single_step = _prepare_single_step_progress()
    if single_step:
        from backend.services.sprint_session import set_sprint_mode

        set_sprint_mode("single_step")
    handler: Optional[str] = None
    active_task: Optional[Dict[str, Any]] = None
    lane_before = ""
    agent_name = "System"

    with state.STATE_LOCK:
        normalize_board_lanes(state.SHARED_BOARD)
        handler, active_task = _select_sprint_step_handler()

    if active_task and active_task.get("id"):
        lane_before = get_task_lane(str(active_task["id"])) or ""
        agent_name = _HANDLER_AGENT.get(handler or "idle", "System")

    _emit_sprint_step_progress(handler or "idle", active_task)
    if handler and handler not in ("idle", "needs_user", "blocked"):
        lane = get_task_lane(active_task["id"]) if active_task else None
        title = active_task.get("title", "?") if active_task else "?"
        add_system_log(
            "System",
            "info",
            f"Sprint handler: {handler} — '{title}' ({lane or 'n/a'})",
        )

    with state.STATE_LOCK:
        needs_user_count = len(state.SHARED_BOARD.get("Needs User", []))
    if needs_user_count and handler != "needs_user":
        add_system_log(
            "System",
            "info",
            f"{needs_user_count} task(s) in Needs User — continuing other lanes this step.",
        )

    if handler not in ("idle", "needs_user", "blocked", "dev_recovery"):
        exclude = str(active_task.get("id") or "") if active_task else ""
        _side_park_latched_needs_po(brief, exclude_id=exclude)

    if handler and handler not in ("idle", "needs_user", "blocked") and active_task:
        _start_sprint_session(handler, active_task)

    try:
        if handler == "po" and active_task:
            _run_po_clarification(active_task, brief)
        elif handler == "needs_user":
            add_system_log("System", "info", "Feature waiting in Needs User — resolve via UI.")
        elif handler == "dev" and active_task:
            _run_developer_step(active_task, brief)
        elif handler == "dev_recovery" and active_task:
            _recover_latched_dev_card(active_task, brief)
        elif handler == "refinement_dev" and active_task:
            _run_refinement_dev_review(active_task, brief)
        elif handler == "spike_dev" and active_task:
            _run_spike_dev(active_task, brief)
        elif handler == "refinement_po" and active_task:
            _run_refinement_po_update(active_task, brief)
        elif handler == "blocked":
            _log_idle_dependency_status()
        elif handler == "cr" and active_task:
            _run_code_review_step(active_task, brief)
        elif handler == "qa" and active_task:
            _run_qa_step(active_task, brief)
        elif handler == "idle":
            _log_idle_dependency_status()
            add_system_log("System", "warning", "No active features. Send brief to PO or add a feature.")
    except Exception as exc:
        if active_task and active_task.get("id"):
            add_system_log(
                "System",
                "warning",
                f"Sprint step interrupted: {exc}",
            )
            _ensure_interrupted_step_recorded(
                str(active_task["id"]),
                lane_before or get_task_lane(str(active_task["id"])) or "",
                agent_name,
            )
        raise
    finally:
        _finish_sprint_session(handler)
        clear_active_sprint_context()
        state.SPRINT_STEP_STARTED_AT = None
        with state.STATE_LOCK:
            if active_task and active_task.get("id"):
                try:
                    from backend.services.task_qa_markdown import update_task_qa_markdown

                    update_task_qa_markdown(str(active_task["id"]))
                except Exception:
                    pass
                try:
                    from backend.services.task_spec_markdown import update_task_spec_markdown

                    update_task_spec_markdown(str(active_task["id"]))
                except Exception:
                    pass
            save_current_project_state(project_id=state.CURRENT_PROJECT_ID)
            if active_task and active_task.get("id"):
                publish_board_delta(str(active_task["id"]), source="sprint_step")
            else:
                publish_board_update(source="sprint_step")
        if single_step:
            if active_task and active_task.get("id") and handler not in ("idle", "needs_user", "blocked"):
                tid = str(active_task["id"])
                if not _interrupted_outcome_already_recorded(tid):
                    _record_last_step_outcome(
                        tid,
                        lane_before or get_task_lane(tid) or "",
                        agent_name,
                    )
            _finish_single_step_progress(active_task)
        try:
            from backend.services.board_status_digest import notify_board_status_after_step

            notify_board_status_after_step(
                active_task=active_task,
                handler=handler,
                agent=agent_name,
            )
        except Exception:
            pass
        try:
            drain_pending_splits(ollama_url)
        except Exception:
            pass


def run_in_progress_step(
    brief: str,
    ollama_url: str,
    task_id: Optional[str] = None,
    *,
    override_phase_cycle_cap: bool = False,
) -> None:
    """Run Dev on an In Progress card only — skips Needs PO, Backlog, and Refinement."""
    from backend.services.logs import add_system_log

    brief = resolve_brief_for_sprint(brief)
    agent_dev.ollama_url = ollama_url
    agent_po.ollama_url = ollama_url
    agent_qa.ollama_url = ollama_url
    agent_cr.ollama_url = ollama_url

    active_task: Optional[Dict[str, Any]] = None
    with state.STATE_LOCK:
        normalize_board_lanes(state.SHARED_BOARD)
        in_progress = list(state.SHARED_BOARD.get("In Progress", []))
        if task_id:
            needle = str(task_id)
            if not _task_in_lane(needle, "In Progress"):
                raise ValueError(f"Task '{needle}' is not in In Progress")
            active_task = find_task_by_id(needle)
        elif in_progress:
            if not override_phase_cycle_cap:
                in_progress = [
                    task for task in in_progress if not task.get("phaseCycleCapReached")
                ]
            sorted_tasks = sorted(
                in_progress,
                key=lambda t: (t.get("priority") if isinstance(t.get("priority"), (int, float)) else 100, str(t.get("id", ""))),
            )
            active_task = dict(sorted_tasks[0])
        else:
            raise ValueError("No cards in In Progress")

    if not active_task:
        raise ValueError("No runnable cards in In Progress")
    if active_task.get("phaseCycleCapReached"):
        if not override_phase_cycle_cap:
            raise ValueError(
                "Task is blocked by the phase cycle cap; split, clarify, or explicitly reset it"
            )
        from backend.services.sprint_speed_gates import reset_dev_cycle_latch

        reset_dev_cycle_latch(active_task)

    from backend.services.sprint_session import set_sprint_mode

    set_sprint_mode("in_progress")
    _prepare_single_step_progress(force=True)
    tid = str(active_task.get("id", ""))
    title = str(active_task.get("title", tid))
    lane_before = get_task_lane(tid) or "In Progress"
    add_system_log(
        "System",
        "info",
        f"Sprint handler: dev (in-progress-only) — '{title}' (In Progress)",
    )
    _emit_sprint_step_progress("dev", active_task)

    _ensure_dev_step_trace(tid, title, lane_before)
    _start_sprint_session("dev", active_task, sprint_mode="in_progress")

    try:
        _run_developer_step(dict(active_task), brief)
    except Exception:
        _ensure_interrupted_step_recorded(tid, lane_before, "Developer")
        raise
    finally:
        _finish_sprint_session("dev")
        clear_active_sprint_context()
        state.SPRINT_STEP_STARTED_AT = None
        with state.STATE_LOCK:
            try:
                from backend.services.task_qa_markdown import update_task_qa_markdown

                update_task_qa_markdown(tid)
            except Exception:
                pass
            try:
                from backend.services.task_spec_markdown import update_task_spec_markdown

                update_task_spec_markdown(tid)
            except Exception:
                pass
            save_current_project_state(project_id=state.CURRENT_PROJECT_ID)
            publish_board_delta(tid, source="sprint_step")
        if not _interrupted_outcome_already_recorded(tid):
            _record_last_step_outcome(tid, lane_before, "Developer")
        _finish_single_step_progress(active_task)
        try:
            drain_pending_splits(ollama_url)
        except Exception:
            pass


def _build_sprint_summary(steps: int, status: str = "completed") -> Dict[str, Any]:
    board = state.SHARED_BOARD
    completed = [t.get("id") for t in board.get("Done", [])]
    qa_failed = [
        t.get("id")
        for lane in board.values()
        for t in lane
        if isinstance(t, dict) and t.get("qaFailure")
    ]
    blocked = [
        t.get("id")
        for t in board.get("Backlog", [])
        if not task_dependencies_met(t)
    ]
    summary = {
        "stepsRun": steps,
        "completed": completed[-10:],
        "qaFailed": qa_failed,
        "blocked": blocked,
        "needsPo": len(board.get("Needs PO", [])),
        "needsUser": len(board.get("Needs User", [])),
        "status": status,
    }
    # Sprint is over (finished, idle, or cancelled) — never leave agents on a backup model.
    try:
        from backend.services.backup_model import restore_all_primary_models

        restore_all_primary_models(reason=f"sprint {status}")
    except Exception:
        pass
    save_sprint_summary(summary)
    try:
        from backend.services.sprint_report import finalize_sprint_report

        finalize_sprint_report(steps, status)
    except Exception:
        pass
    publish_event("sprint", summary)
    try:
        from backend.services.phone_notify import notify_if_enabled

        notify_if_enabled(
            "sprint_end",
            f"Sprint {status}",
            (
                f"steps={steps} · Needs User={summary['needsUser']} · "
                f"Needs PO={summary['needsPo']} · QA fail={len(qa_failed)}"
            ),
            task_id=f"sprint:{status}:{steps}",
        )
    except Exception:
        pass
    return summary


def _recover_zero_work_stall(task_id: str, exit_reason: str, brief: str) -> str:
    """Autonomous recovery ladder when auto sprint hits zero-work watchdog."""
    live = _live_board_task(task_id)
    if not live:
        return "missing"

    live.pop("lastNextWorkKey", None)
    live.pop("lastNextWorkNoWrite", None)
    live["consecutiveNoWriteStall"] = 0

    ws = get_workflow_settings()
    max_stuck = int(ws.get("maxStuckSteps", 3) or 3)
    reason_snip = str(exit_reason or "")[:120]

    if _should_attempt_stuck_auto_split(live, ws):
        if _run_stuck_auto_split(task_id, live, max_stuck):
            add_system_log(
                "System",
                "info",
                f"{task_id}: zero-work recovery — auto-split succeeded ({reason_snip})",
            )
            return "auto_split"
        live = _live_board_task(task_id) or live

    max_po = int(ws.get("maxPoRoundTrips", 3) or 3)
    if int(live.get("poRoundTrips") or 0) < max_po:
        po_msg = (
            f"Auto sprint stalled with no model progress ({reason_snip}). "
            "Product Owner should refine the spec or split the card."
        )
        if _redirect_to_needs_po(task_id, live, po_msg, kind="stuck_loop"):
            add_system_log(
                "System",
                "info",
                f"{task_id}: zero-work recovery — routed to Needs PO",
            )
            return "needs_po"

    live = _live_board_task(task_id) or live
    if live and live.get("phaseCycleCapReached"):
        _recover_latched_dev_card(live, brief, quiet=True)
        add_system_log(
            "System",
            "info",
            f"{task_id}: zero-work recovery — latched card recovery attempted",
        )
        return "latched_recovery"

    live = _live_board_task(task_id) or live
    if live:
        park_msg = (
            f"Repeated zero-work stall ({reason_snip}). "
            "Split the card or reset the Developer visit latch."
        )
        if _try_move_to_needs_user(task_id, live, park_msg, kind="phase_cycle_cap"):
            add_system_log(
                "System",
                "warning",
                f"{task_id}: zero-work recovery — parked in Needs User (last resort)",
            )
            return "needs_user"
        reroute_tool_blocker_to_dev(task_id, live, reason_snip)
        add_system_log(
            "System",
            "info",
            f"{task_id}: zero-work recovery — cleared gates for Developer retry",
        )
        return "reroute_dev"

    return "none"


def run_auto_sprint(
    brief: str,
    ollama_url: str,
    max_steps: int | None = None,
    *,
    continue_report: bool = False,
) -> Dict[str, Any]:
    from backend.services.sprint_session import set_sprint_mode

    set_sprint_mode("auto")
    state.AUTO_SPRINT_ACTIVE = True
    try:
        return _run_auto_sprint_body(
            brief, ollama_url, max_steps, continue_report=continue_report
        )
    finally:
        state.AUTO_SPRINT_ACTIVE = False


def _run_auto_sprint_body(
    brief: str,
    ollama_url: str,
    max_steps: int | None = None,
    *,
    continue_report: bool = False,
) -> Dict[str, Any]:
    import time

    state.SPRINT_CANCEL = False
    state.SPRINT_CANCEL_INTENT = None
    state.SPRINT_NEEDS_USER_COUNT = 0
    try:
        from backend.services.sprint_report import begin_sprint_report

        begin_sprint_report(restart=not continue_report)
    except Exception:
        pass
    brief = resolve_brief_for_sprint(brief)
    from backend.services.backlog_preflight import log_backlog_preflight_warnings

    log_backlog_preflight_warnings()
    ws = get_workflow_settings()
    limit = max_steps if max_steps is not None else int(ws.get("maxSprintSteps", 20))
    state.SPRINT_PROGRESS_MAX = limit
    publish_sprint_progress(
        phase="sprint_step",
        step=0,
        max_steps=limit,
        agent="System",
        task_title="Auto sprint starting…",
        status="starting",
    )
    steps = 0
    status = "completed"
    session_start = time.monotonic()
    refresh_enabled = bool(ws.get("autoSprintSessionRefreshEnabled", True))
    refresh_minutes = int(ws.get("autoSprintSessionRefreshMinutes") or 60)
    refresh_sec = max(60, refresh_minutes * 60)
    zero_work_watchdog: Dict[str, Any] = {}
    zero_work_recovery_attempts = 0
    raw_max_recovery = ws.get("maxZeroWorkRecoveryAttempts")
    if raw_max_recovery is None:
        raw_max_recovery = 2
    max_zero_work_recovery = max(0, int(raw_max_recovery))
    saw_ollama = False

    try:
        from backend.services.sprint_speed_gates import reset_interrupt_backoff_state

        reset_interrupt_backoff_state()
    except Exception:
        pass
    if ws.get("warmModelOnSprintStart", True):
        try:
            from backend import state as app_state
            from backend.agents.registry import agent_dev
            from backend.services.ollama_warmup import warm_model

            warm_model(str(getattr(agent_dev, "model", "") or ""))
            add_system_log("System", "info", "Warmed primary model for sprint start")
        except Exception:
            pass

    while steps < limit and not state.SPRINT_CANCEL:
        with state.STATE_LOCK:
            if not has_sprint_work():
                status = "idle"
                break
        # Back off when prior tick crashed before any Ollama call (interrupt storm).
        try:
            from backend.services.sprint_speed_gates import remaining_interrupt_backoff_sec

            wait_sec = remaining_interrupt_backoff_sec()
            if wait_sec > 0:
                add_system_log(
                    "System",
                    "warning",
                    f"Auto sprint interrupt backoff — sleeping {wait_sec:.1f}s before next step",
                )
                slept = 0.0
                while slept < wait_sec and not state.SPRINT_CANCEL:
                    chunk = min(1.0, wait_sec - slept)
                    time.sleep(chunk)
                    slept += chunk
                if state.SPRINT_CANCEL:
                    break
        except Exception:
            pass
        state.SPRINT_PROGRESS_STEP = steps + 1
        # Optional: pipeline independent In Progress cards (prefetch next while current runs).
        batch_ran = 0
        try:
            batch_ran = _run_parallel_independent_dev_batch(brief, ollama_url)
        except Exception:
            batch_ran = 0
        if batch_ran > 0:
            steps += batch_ran
        else:
            try:
                run_sprint_step(brief, ollama_url)
            except Exception as exc:
                add_system_log(
                    "System",
                    "warning",
                    f"Sprint step raised ({exc}) — treating as interrupted",
                )
            steps += 1
        try:
            from backend.services.sprint_speed_gates import note_early_interrupt

            outcome = state.LAST_STEP_OUTCOME if isinstance(state.LAST_STEP_OUTCOME, dict) else {}
            diag = state.LAST_STEP_DIAGNOSTICS if isinstance(state.LAST_STEP_DIAGNOSTICS, dict) else {}
            diag_reason = str(diag.get("exitReason") or "")
            out_reason = str(outcome.get("exitReason") or "")
            watch_reason = (
                diag_reason if diag_reason == "interrupted" else (out_reason or diag_reason)
            )
            delay = note_early_interrupt(
                exit_reason=watch_reason,
                ollama_call_count=int(
                    diag.get("ollamaCallCount")
                    or len(diag.get("ollamaCalls") or [])
                    or 0
                ),
                duration_ms=int(diag.get("durationMs") or 0),
                tool_call_count=int(
                    diag.get("toolCallCount")
                    or len(diag.get("toolsLog") or diag.get("toolsUsed") or [])
                    or 0
                ),
            )
            if delay > 0:
                add_system_log(
                    "System",
                    "warning",
                    f"Early interrupted step (0 Ollama calls) — next backoff {delay:.1f}s",
                )
        except Exception:
            pass
        outcome = state.LAST_STEP_OUTCOME if isinstance(state.LAST_STEP_OUTCOME, dict) else {}
        diag = state.LAST_STEP_DIAGNOSTICS if isinstance(state.LAST_STEP_DIAGNOSTICS, dict) else {}
        diag_reason = str(diag.get("exitReason") or "")
        out_reason = str(outcome.get("exitReason") or "")
        reason = diag_reason if diag_reason == "interrupted" else (out_reason or diag_reason)
        task_id = str(diag.get("taskId") or outcome.get("taskId") or "")
        ollama_calls = int(diag.get("ollamaCallCount") or len(diag.get("ollamaCalls") or []) or 0)
        tool_calls = int(
            diag.get("toolCallCount")
            or len(diag.get("toolsLog") or diag.get("toolsUsed") or [])
            or 0
        )
        if ollama_calls > 0:
            saw_ollama = True
        from backend.services.sprint_speed_gates import note_zero_work_exit

        if note_zero_work_exit(
            zero_work_watchdog,
            task_id=task_id,
            exit_reason=reason,
            ollama_call_count=ollama_calls,
            tool_call_count=tool_calls,
            ws=ws,
        ):
            with state.STATE_LOCK:
                still_work = has_sprint_work()
            if (
                still_work
                and zero_work_recovery_attempts < max_zero_work_recovery
                and task_id
            ):
                recovery_action = _recover_zero_work_stall(task_id, reason, brief)
                zero_work_recovery_attempts += 1
                zero_work_watchdog.clear()
                add_system_log(
                    "System",
                    "warning",
                    f"Auto sprint zero-work streak for {task_id} ({reason}) — "
                    f"recovery {zero_work_recovery_attempts}/{max_zero_work_recovery}: "
                    f"{recovery_action}; continuing.",
                )
                continue
            status = "retry_watchdog"
            add_system_log(
                "System",
                "warning",
                f"Auto sprint paused after {zero_work_watchdog.get('streak')} zero-work exits for "
                f"{task_id} ({reason}); no fourth retry scheduled.",
            )
            break
        if refresh_enabled and (time.monotonic() - session_start) >= refresh_sec:
            status = "session_refresh"
            add_system_log(
                "System",
                "info",
                f"Auto sprint session refresh after {refresh_minutes} minute(s) — "
                "finished current step; UI will reload and resume.",
            )
            break
        from backend.services.simulation_gate import has_pending_simulation

        if has_pending_simulation():
            status = "simulation_pending"
            add_system_log(
                "System",
                "info",
                "Auto sprint paused — waiting for offline simulation confirm in the UI.",
            )
            break

    if (
        status not in (
            "cancelled",
            "idle",
            "retry_watchdog",
            "session_refresh",
            "simulation_pending",
        )
        and not saw_ollama
    ):
        with state.STATE_LOCK:
            still_work = has_sprint_work()
        if not still_work:
            status = "idle"
            add_system_log(
                "System",
                "info",
                "Auto sprint paused — no model work and no remaining actionable cards.",
            )

    if state.SPRINT_CANCEL:
        status = "cancelled"
        add_system_log("System", "info", "Auto sprint cancelled.")
        publish_sprint_progress(
            phase="cancelled",
            step=steps,
            max_steps=limit,
            agent="System",
            task_title="Sprint cancelled",
        )
    elif status != "idle" and steps >= limit:
        status = "max_steps"
        add_system_log("System", "info", f"Auto sprint finished after {steps} step(s) (max steps).")
    elif status == "session_refresh":
        add_system_log(
            "System",
            "info",
            f"Auto sprint paused for session refresh after {steps} step(s).",
        )
    elif status == "idle":
        add_system_log("System", "info", "Auto sprint paused — no backlog work remaining.")
    else:
        add_system_log("System", "info", f"Auto sprint finished after {steps} step(s).")
    return _build_sprint_summary(steps, status)


def run_plan_and_run(
    brief: str,
    ollama_url: str,
    max_steps: int | None = None,
    *,
    execution_profile: str | None = None,
    skip_preflight: bool = False,
) -> Dict[str, Any]:
    from backend.services.plan_run_orchestration import (
        apply_plan_run_execution_profile,
        ensure_implementer_card_from_brief,
        should_skip_po_plan_for_plan_run,
    )
    from backend.services.plan_run_preflight import validate_plan_run_preflight

    ws = get_workflow_settings()
    limit = max_steps if max_steps is not None else int(ws.get("maxSprintSteps", 20))
    state.SPRINT_CANCEL = False
    state.SPRINT_CANCEL_INTENT = None
    state.SPRINT_NEEDS_USER_COUNT = 0
    state.SPRINT_PROGRESS_MAX = limit
    state.SPRINT_PROGRESS_STEP = 0
    try:
        from backend.services.sprint_report import begin_sprint_report

        begin_sprint_report()
    except Exception:
        pass

    profile = apply_plan_run_execution_profile(override=execution_profile, persist=True)

    if not skip_preflight:
        preflight = validate_plan_run_preflight(brief, ws=ws)
        if preflight.get("blocked"):
            issues = preflight.get("issues") or []
            fail_msgs = [i.get("message") for i in issues if i.get("severity") == "fail"]
            detail = "; ".join(str(m) for m in fail_msgs if m) or "Plan & Run preflight failed"
            add_system_log("System", "warning", f"Plan & Run blocked: {detail}")
            summary = _build_sprint_summary(0, "preflight_blocked")
            summary["preflight"] = preflight
            publish_sprint_progress(
                phase="done",
                step=0,
                max_steps=limit,
                agent="System",
                task_title="Plan & Run blocked (preflight)",
            )
            return summary

    publish_sprint_progress(
        phase="po_plan",
        step=0,
        max_steps=limit,
        agent="Product Owner",
        task_id=PLANNING_TASK_ID,
        task_title="Plan & Run started",
        lane="Backlog",
    )
    add_system_log(
        "System",
        "info",
        f"Plan & Run started — profile={profile}; "
        + ("skipping PO plan (actionable brief/backlog)" if should_skip_po_plan_for_plan_run(brief, ws) else "PO planning, then sprint steps…"),
    )

    planned = True
    if should_skip_po_plan_for_plan_run(brief, ws):
        ok, detail = ensure_implementer_card_from_brief(brief)
        planned = ok
        if ok:
            from backend.services.project_service import save_current_project_state

            save_current_project_state()
        if not ok:
            add_system_log("System", "warning", f"Plan & Run: could not bootstrap card ({detail})")
    else:
        planned = run_po_plan(brief, ollama_url)

    from backend.services.simulation_gate import has_pending_simulation

    if state.SPRINT_CANCEL or not planned:
        status = "cancelled" if state.SPRINT_CANCEL else "llm_failed"
        if has_pending_simulation():
            status = "simulation_pending"
        summary = _build_sprint_summary(0, status)
        publish_sprint_progress(
            phase="cancelled" if state.SPRINT_CANCEL else "done",
            step=0,
            max_steps=limit,
            agent="System",
            task_title="Plan & Run stopped",
        )
        return summary

    summary = run_auto_sprint(brief, ollama_url, max_steps=max_steps, continue_report=True)
    publish_sprint_progress(
        phase="done",
        step=int(summary.get("stepsRun", 0)),
        max_steps=limit,
        agent="System",
        task_title=f"Plan & Run finished ({summary.get('status', 'completed')})",
    )
    return summary
