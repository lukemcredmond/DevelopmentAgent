import json
import os
import random
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

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
    build_stuck_escalation_message,
    dev_clarification_from_result,
    dev_explicit_needs_user,
    prefer_po_instruction_suffix,
    should_escalate_to_needs_user,
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

_HANDLER_AGENT: Dict[str, str] = {
    "po": "Product Owner",
    "dev": "Developer",
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
        if name == "read_file":
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


def _outcome_why_card_stayed(
    stop_reason: str,
    *,
    title: str,
    lane_after: str,
    plan_rejections: int = 0,
    text_rejections: int = 0,
) -> str:
    if lane_after != "In Progress":
        return ""
    if stop_reason == "completed_with_writes":
        return ""
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
    if text_rejections or plan_rejections:
        return (
            f"Step on '{title}' ended with {plan_rejections} plan and {text_rejections} text "
            f"rejections and no file edits. {base}"
        )
    return f"Card stayed in {lane_after} on '{title}'. {base}"


def _outcome_suggested_action(stop_reason: str, lane_after: str) -> str:
    if lane_after != "In Progress":
        return ""
    if stop_reason == "completed_with_writes":
        return ""
    return "Run In Progress again or edit the workspace files manually, then move the card to QA."


def _build_last_step_outcome(
    task_id: str,
    lane_before: str,
    agent: str,
    *,
    agent_result: Optional[str] = None,
) -> Dict[str, Any]:
    from backend.services.step_diagnostics import get_active_trace

    task = find_task_by_id(task_id)
    lane_after = get_task_lane(task_id) or lane_before
    tool_failures = _count_task_tool_failures(task) if task else 0
    title = str(task.get("title", task_id)) if task else task_id
    trace = get_active_trace()
    plan_rejections = trace.plan_rejections if trace else 0
    text_rejections = trace.text_rejections if trace else 0
    tools_used = sorted(trace.tools_used) if trace else []
    agent_snippet = (agent_result or "")[:200]
    if trace and not agent_snippet and trace.events:
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
    )
    suggested_action = _outcome_suggested_action(stop_reason, lane_after)
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

    outcome: Dict[str, Any] = {
        "taskId": task_id,
        "agent": agent,
        "laneBefore": lane_before,
        "laneAfter": lane_after,
        "toolFailures": tool_failures,
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
    progress = state.LAST_STEP_PROGRESS
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
        from backend.services.step_diagnostics import store_step_progress

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
    return {k: v for k, v in compact.items() if v not in (None, "", [], {})}


def _record_last_step_outcome(
    task_id: str,
    lane_before: str,
    agent: str,
    *,
    agent_result: Optional[str] = None,
) -> None:
    state.LAST_STEP_OUTCOME = _build_last_step_outcome(
        task_id,
        lane_before,
        agent,
        agent_result=agent_result or state.LAST_AGENT_STEP_RESULT,
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
        }
    state.DEV_STEP_READ_ONLY_NO_EDITS = False
    state.DEV_STEP_COMMAND_REPEAT_NO_PROGRESS = False
    state.DEV_STEP_INTERRUPTED = False


def _finalize_step_diagnostics_if_traced(task_id: str) -> None:
    from backend.services.step_diagnostics import finalize_active_step_trace

    lane_after = get_task_lane(task_id) or ""
    finalize_active_step_trace(lane_after=lane_after)


def _ensure_dev_step_trace(task_id: str, task_title: str, lane_before: str) -> None:
    from backend.services.step_diagnostics import get_active_trace, start_step_trace

    if get_active_trace() is None:
        start_step_trace(task_id, task_title, "Developer", lane_before)


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
    bt = "```"
    json_blocks = re.findall(rf"{bt}json\s*(.*?)\s*{bt}", text, re.DOTALL)
    for block in json_blocks:
        try:
            parsed = json.loads(block.strip())
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            continue
    try:
        parsed = json.loads(text.strip())
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass
    return None


def _task_in_lane(task_id: str, lane: str) -> bool:
    needle = str(task_id)
    return needle in [str(t.get("id", "")) for t in state.SHARED_BOARD.get(lane, [])]


def _dev_needs_po(result: str, task: Optional[Dict[str, Any]] = None) -> bool:
    """Stricter escalation detection — avoid substring false positives."""
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


def _set_needs_user_fields(task: Dict[str, Any], msg: str) -> None:
    """Populate structured Needs User fields from escalation message."""
    text = str(msg or "").strip()
    task["userQuestion"] = text[:500]
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if lines:
        task["needsUserReason"] = lines[0][:240]
        task["needsUserAction"] = (lines[1] if len(lines) > 1 else lines[0])[:240]
    else:
        task["needsUserReason"] = "Agent requires your input to continue."
        task["needsUserAction"] = "Review the task and provide a decision or missing information."


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


def _try_move_to_needs_user(
    task_id: str,
    task: Dict[str, Any],
    msg: str,
    *,
    kind: str = "stuck_loop",
) -> bool:
    allowed, block_reason = should_escalate_to_needs_user(task, msg)
    if not allowed:
        if block_reason == "clarification_use_po":
            return _redirect_to_needs_po(task_id, task, msg, kind=kind)
        if block_reason in (
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
        return False
    if _needs_user_cap_reached():
        add_system_log(
            "System",
            "warning",
            f"{task_id}: Needs User blocked by autonomous cap ({state.SPRINT_NEEDS_USER_COUNT}) — {msg[:120]}",
        )
        return False
    _set_needs_user_fields(task, msg)
    move_board_stage(task_id, "Needs User")
    state.SPRINT_NEEDS_USER_COUNT += 1
    publish_activity(
        task_id,
        kind,
        msg,
        role="system",
        agent="System",
        lane="Needs User",
    )
    add_system_log("System", "warning", f"{task_id}: {msg}")
    try:
        from backend.services.phone_notify import notify_if_enabled

        title = task.get("title") or task_id
        reason = task.get("needsUserReason") or msg
        action = str(task.get("needsUserAction") or "").strip()
        body_parts = [f"[{task_id}] {title}", str(reason)[:350]]
        if action:
            body_parts.append(f"Action: {action[:200]}")
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
        try:
            from backend.agents.tool_fingerprints import clear_fingerprint_escalation_state

            clear_fingerprint_escalation_state(task)
        except Exception:
            pass
        try:
            from backend.services.backup_model import clear_backup_remaining, restore_primary_model
            from backend.agents.registry import agent_cr, agent_dev, agent_po, agent_qa

            clear_backup_remaining(task)
            # Restore all agents to primary after a successful lane move
            restore_primary_model(agent_po, "po")
            restore_primary_model(agent_dev, "dev")
            restore_primary_model(agent_cr, "cr")
            restore_primary_model(agent_qa, "qa")
        except Exception:
            pass
        return

    task["stuckLoops"] = int(task.get("stuckLoops", 0)) + 1

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

    task["stuckLoops"] = 0

    # Do not yank cards already escalated / finished.
    if lane_after in ("Needs PO", "Needs User", "Done", "Features"):
        return

    # Ladder: backup already armed above → try one auto-split before Needs PO.
    # Skip auto-split for lint/tool walls (lint fanout covers "break into bits").
    if (
        ws.get("enableSplitOnStuck", True)
        and not task.get("splitAttemptedOnStuck")
        and not stuck_is_tool_or_lint(task)
    ):
        task["splitAttemptedOnStuck"] = True
        record_task_decision(
            task_id,
            "System",
            "stuck_split",
            "Auto-split after stuck steps (backup tried first)",
            f"stuckLoops reached {max_stuck} — attempting PO split before Needs PO",
        )
        try:
            from backend.agents.registry import agent_dev, agent_po

            ollama_url = (
                str(getattr(agent_dev, "ollama_url", "") or "").strip()
                or str(getattr(agent_po, "ollama_url", "") or "").strip()
                or "http://localhost:11434"
            )
            split_result = run_po_split_task(
                task_id,
                ollama_url,
                guidance=(
                    "Auto-split: agents stuck after backup model attempts — "
                    "break into 2–5 smallest cards."
                ),
            )
            added = int((split_result or {}).get("added") or 0)
            lane_now = get_task_lane(task_id) or lane_after
            if added > 0 or lane_now != lane_after:
                try:
                    from backend.services.backup_model import (
                        clear_backup_remaining,
                        restore_primary_model,
                    )
                    from backend.agents.registry import agent_cr, agent_qa

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
                return
            add_system_log(
                "System",
                "warning",
                f"{task_id}: stuck auto-split added no cards — escalating to Needs PO",
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

    max_po = int(ws.get("maxPoRoundTrips", 3))
    msg = build_stuck_escalation_message(task, lane_after, max_stuck)
    if int(task.get("poRoundTrips", 0)) >= max_po:
        if stuck_is_tool_or_lint(task):
            record_task_decision(
                task_id,
                "System",
                "stuck_loop",
                f"Lint/tool blocker after {max_stuck} steps — not escalating to Needs User",
                msg,
            )
            add_system_log(
                "System",
                "warning",
                f"{task_id}: stuck on lint/tools — fix code or run diagnosis; not moving to Needs User",
            )
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
    return "max tool iterations" in lower or "max_iterations" in lower


def _maybe_auto_extend_dev_step(
    task_id: str,
    task: Dict[str, Any],
    result: str,
) -> str:
    """
    One auto-extend on max_iterations when progress is evident (writes or tools),
    skipping loop-stop exits. Sets task.autoExtendUsed for the stuck cycle.
    """
    ws = get_workflow_settings()
    if not ws.get("autoExtendOnMaxIter", True):
        return result
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
        for marker in ("duplicate tool", "timed out", "tool failure", "same failing")
    ):
        return result
    if stop in _NO_AUTO_EXTEND_STOPS:
        return result

    tools_raw = (progress or {}).get("toolsUsed") or []
    tools = {str(t) for t in tools_raw} if isinstance(tools_raw, (list, set, tuple)) else set()
    has_writes = bool(tools & _WRITE_TOOLS) or _task_has_write_files(task)
    has_tools = bool(tools)
    if not (has_writes or has_tools):
        return result

    extra = max(1, min(int(ws.get("autoExtendExtraIterations") or 4), 16))
    add_system_log(
        "Developer",
        "info",
        f"Auto-extend +{extra} iterations (progress detected)",
    )
    task["autoExtendUsed"] = True
    try:
        from backend.services.prompt_retry import extend_agent_step

        ollama_url = str(getattr(state, "OLLAMA_URL", "") or "http://localhost:11434")
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
    from backend.services.prompt_sections import FocusContext, compose_prompt

    ws = get_workflow_settings()
    local_slm = is_local_slm_profile(ws)
    preload = local_slm_sprint_preload_enabled(ws)
    num_ctx = initial_ollama_num_ctx(agent_role)
    semantic_block, sem_paths = "", []
    graph_block = ""
    file_block, file_paths = "", []
    context_block = ""
    graph_used = False
    if preload:
        budgets = sprint_preload_budgets(num_ctx, local_slm=local_slm)
        top_k_override = None
        if local_slm:
            top_k_override = min(max(1, int(ws.get("semanticSprintTopK") or 5)), 2)
        semantic_block, sem_paths = build_semantic_sprint_context(
            active_task,
            max_chars=budgets["semantic"],
            top_k_override=top_k_override,
        )
        graph_block = ""
        try:
            from backend.services.graphify_service import build_graphify_sprint_context, graphify_status

            if graphify_status().get("available") or graphify_status().get("reportExists"):
                graph_block = build_graphify_sprint_context(
                    active_task,
                    max_chars=budgets["graph"],
                )
                graph_used = bool(graph_block)
        except Exception:
            graph_block = ""
        total_budget = budgets["total"]
        file_budget = max(1000, total_budget - len(semantic_block) - len(graph_block)) if (semantic_block or graph_block) else total_budget
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
    if agent_role == "Developer" and preload:
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
    else:
        base = build_task_prompt(active_task, brief, agent_role=agent_role)
    if not local_slm and agent_role == "Developer":
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
    if agent_role == "Developer" and not local_slm:
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
    evidence = task.get("qaEvidence") or {}
    if evidence.get("userOverride"):
        return False, ""
    step_started = state.SPRINT_STEP_STARTED_AT or ""
    if evidence.get("playbookRun") and not evidence.get("passed"):
        return True, "Automated test playbook failed — cannot move to Done."
    if not _qa_has_test_evidence(task, evidence, step_started):
        return True, "No test evidence — run_test or run_command must succeed before Done."
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

    save_current_project_state()
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
    from backend.services.subtask_service import subtask_gate_blocks_advance

    blocked, reason = subtask_gate_blocks_advance(task)
    if blocked:
        return blocked, reason
    if not _task_has_write_files(task):
        return (
            True,
            "No files written — stay In Progress until apply_patch/write_file.",
        )
    if get_workflow_settings().get("requireWorkspaceStructure", True):
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
    if not get_workflow_settings().get("requireCleanLint"):
        return False, ""
    diagnostics = task.get("lastCommandDiagnostics") or []
    if diagnostics:
        return True, f"Unresolved lint: {len(diagnostics)} problem(s) — fix before advancing."
    status = _dev_verification_status(task, state.SPRINT_STEP_STARTED_AT or "")
    if status == "ran_with_findings":
        return True, "Lint/test command reported findings — resolve before advancing."
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
    return "Code Review" if get_workflow_settings().get("requireCodeReview") else "QA"


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


def _llm_iterations() -> int:
    return int(get_workflow_settings().get("maxLlmIterationsPerStep", 8))


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
        set_active_sprint_context(PLANNING_TASK_ID, "Product Owner")
        outline = agent_po.execute_step(
            "Produce a concise markdown project plan ONLY — no JSON, no code.\n"
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
            max_iterations=1,
        )
    finally:
        clear_active_sprint_context()

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
    po_output = ""
    try:
        set_active_sprint_context(PLANNING_TASK_ID, "Product Owner")
        po_output = agent_po.execute_step(
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
            max_iterations=1,
        )
    finally:
        clear_active_sprint_context()

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
        )
        if try_defer_simulation(prop):
            return 0

    return _append_po_backlog_from_output(po_output, existing_set)


def run_po_plan(brief: str, ollama_url: str) -> None:
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
        return

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
        return

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
            return

    if po_output:
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

    save_current_project_state()


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
        po_output = agent_po.execute_step(
            f"{po_planning_guidance_block()}{prompt}\n\n"
            "Split this card into 2–5 smaller developer-ready backlog tasks."
            f"{extra}\n"
            f"You MUST call add_backlog_tasks with split_from_task_id={task_id!r}.\n"
            "Invoke the tool yourself — never tell the user to call add_backlog_tasks.\n"
            "If you cannot use tools, reply with ONLY a JSON array (title, description, acceptanceCriteria).",
            max_iterations=_llm_iterations(),
        )

        added = 0
        new_task_ids: List[str] = []
        if _po_chat_used_add_backlog_tool(task_id):
            backlog_after = state.SHARED_BOARD.get("Backlog", [])
            new_task_ids = [t["id"] for t in backlog_after if t["id"] not in backlog_before_ids]
            added = len(new_task_ids)
        elif po_output and po_output != "SIMULATION_FALLBACK":
            added = apply_backlog_from_po_response(po_output, task_id)
            backlog_after = state.SHARED_BOARD.get("Backlog", [])
            new_task_ids = [t["id"] for t in backlog_after if t["id"] not in backlog_before_ids]
        elif po_output == "SIMULATION_FALLBACK":
            from backend.services.simulation_gate import build_proposal, try_defer_simulation

            split_stub = json.dumps(
                [
                    {
                        "title": f"{task.get('title', 'Subtask')} (part 1)",
                        "description": task.get("description", "")[:500],
                        "acceptanceCriteria": ["Deliver first slice of the feature"],
                    },
                    {
                        "title": f"{task.get('title', 'Subtask')} (part 2)",
                        "description": task.get("description", "")[:500],
                        "acceptanceCriteria": ["Deliver remaining scope"],
                    },
                ]
            )
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
                added = apply_backlog_from_po_response(split_stub, task_id)
                backlog_after = state.SHARED_BOARD.get("Backlog", [])
                new_task_ids = [t["id"] for t in backlog_after if t["id"] not in backlog_before_ids]
        else:
            added = 0

        save_current_project_state()
        publish_board_update(task_id, source="split")
        add_system_log(
            "Product Owner",
            "success" if added else "warning",
            f"Split {task_id}: added {added} subtask(s)" if added else f"Split {task_id}: no subtasks added",
        )
        return {"added": added, "taskId": task_id, "taskIds": new_task_ids}
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
    obj = extract_json_object_from_text(result)
    if not obj:
        return False
    description = obj.get("description")
    ac = obj.get("acceptanceCriteria") or obj.get("acceptance_criteria")
    if not description and not ac:
        return False
    apply_po_clarification(
        active_task["id"],
        description=description,
        acceptance_criteria=ac,
    )
    addition = obj.get("briefAddition") or obj.get("brief_addition") or ""
    if addition:
        append_brief_text(addition, "po", f"PO clarification for {active_task['id']}")
    record_brief_changelog("po", f"Clarified {active_task['title']}", result[:300])
    return True


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


def _run_po_clarification(active_task: Dict[str, Any], brief: str) -> None:
    task_id = active_task["id"]
    lane_before = get_task_lane(task_id) or "Needs PO"
    set_active_sprint_context(task_id, "Product Owner")
    try:
        from backend.services.backup_model import apply_model_for_step

        apply_model_for_step(agent_po, "po", find_task_by_id(task_id) or active_task)
    except Exception:
        pass
    add_system_log("Product Owner", "info", f"Clarifying '{active_task['title']}'…")
    task_for_prompt = find_task_by_id(task_id) or active_task
    prompt = (
        build_task_prompt(task_for_prompt, brief)
        + _po_clarification_retry_prompt_block(task_for_prompt)
        + "\nDeveloper needs clarification. Reply with a JSON object: "
        '{"description": "...", "acceptanceCriteria": ["..."], "briefAddition": "..."}\n'
        "Then use update_board to move back to 'In Progress'."
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
                clarified = True
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
            clarified = _apply_po_clarification_result(task, result)
        if not deferred_sim and _task_in_lane(task_id, "Needs PO"):
            if clarified:
                ws = get_workflow_settings()
                if (
                    ws.get("requireBacklogRefinement")
                    and int(task.get("refinementRoundTrips") or 0) > 0
                    and not task.get("refinementComplete")
                ):
                    move_board_stage(task_id, "Refinement")
                    task["refinementStatus"] = "po_updated"
                    publish_activity(
                        task_id,
                        "po_clarified",
                        "PO clarified refinement blockers — returned to Refinement",
                        role="assistant",
                        agent="Product Owner",
                        lane="Refinement",
                    )
                else:
                    move_board_stage(task_id, "In Progress")
                    publish_activity(
                        task_id,
                        "po_clarified",
                        "PO clarified requirements and returned task to Dev",
                        role="assistant",
                        agent="Product Owner",
                        lane="In Progress",
                    )
            else:
                add_system_log(
                    "Product Owner",
                    "warning",
                    f"Clarification incomplete for '{task['title']}' — card stays in Needs PO",
                )
        _check_stuck_and_escalate(task_id, lane_before, agent_key="po")


def _run_developer_step(active_task: Dict[str, Any], brief: str) -> None:
    task_id = active_task["id"]
    lane_before = get_task_lane(task_id) or "In Progress"
    title = str(active_task.get("title", task_id))
    step_started = _mark_sprint_step_start()
    set_active_sprint_context(task_id, "Developer")
    try:
        from backend.services.backup_model import apply_model_for_step

        model_in_use = apply_model_for_step(
            agent_dev, "dev", find_task_by_id(task_id) or active_task
        )
    except Exception:
        model_in_use = str(getattr(agent_dev, "model", "") or "")
    try:
        from backend.services.workspace_scaffold import maybe_auto_scaffold

        maybe_auto_scaffold(find_task_by_id(task_id) or active_task)
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
        prompt = _inject_sprint_context(active_task, brief, "Developer", instructions)
        from backend.services.fix_verify_loop import run_fix_verify_loop

        result = run_fix_verify_loop(
            agent_dev,
            active_task,
            prompt,
            max_iterations=_llm_iterations(),
        )
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
                add_system_log(
                    "Developer",
                    "warning",
                    f"'{task.get('title', task_id)}': dev step read files but made no edits — staying In Progress",
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

                if apply_dev_offline_if_file_exists(task, task_id=task_id, lane_before=lane_before):
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
                        if fresh and _task_has_write_files(fresh):
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
                                    save_current_project_state()
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
        state.DEV_STEP_INTERRUPTED = True
        raise
    finally:
        _finalize_dev_step_diagnostics_if_auto_sprint(task_id, lane_before)


def _run_code_review_step(active_task: Dict[str, Any], brief: str) -> None:
    task_id = active_task["id"]
    lane_before = get_task_lane(task_id) or "Code Review"
    _mark_sprint_step_start()
    set_active_sprint_context(task_id, "Code Reviewer")
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


def _run_qa_step(active_task: Dict[str, Any], brief: str) -> None:
    task_id = active_task["id"]
    lane_before = get_task_lane(task_id) or "QA"
    step_started = _mark_sprint_step_start()
    set_active_sprint_context(task_id, "QA Tester")
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
        reason = f"Circular dependency detected ({path or tid}). Untangle blockedBy links."
    else:
        missing = issues.get("missing") or []
        reason = (
            f"Blocked by missing cards: {', '.join(missing)}. "
            "Remove invalid blockedBy ids or create the dependency cards."
        )
    task["userQuestion"] = reason[:500]
    move_board_stage(tid, "Needs User")
    record_task_decision(tid, "System", "escalation", reason[:300])
    add_system_log("System", "warning", f"{tid}: {reason}")
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


def has_sprint_work() -> bool:
    """True when auto-sprint has actionable (not merely blocked) work."""
    board = state.SHARED_BOARD
    if board.get("Needs PO") or board.get("In Progress"):
        return True
    ws = get_workflow_settings()
    if ws.get("requireCodeReview") and board.get("Code Review"):
        return True
    if board.get("QA"):
        return True
    if next_claimable_backlog_task() or next_po_planning_backlog_task():
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
        if state.SHARED_BOARD.get("Needs PO"):
            active_task = dict(state.SHARED_BOARD["Needs PO"][0])
            handler = "po"
        elif (
            get_workflow_settings().get("pauseSprintOnNeedsUser")
            and state.SHARED_BOARD.get("Needs User")
        ):
            handler = "needs_user"
        elif state.SHARED_BOARD.get("In Progress"):
            active_task = dict(state.SHARED_BOARD["In Progress"][0])
            handler = "dev"
        else:
            handler = None
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

    if handler and handler not in ("idle", "needs_user", "blocked") and active_task:
        _start_sprint_session(handler, active_task)

    try:
        if handler == "po" and active_task:
            _run_po_clarification(active_task, brief)
        elif handler == "needs_user":
            add_system_log("System", "info", "Feature waiting in Needs User — resolve via UI.")
        elif handler == "dev" and active_task:
            _run_developer_step(active_task, brief)
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
            save_current_project_state()
            if active_task and active_task.get("id"):
                publish_board_delta(str(active_task["id"]), source="sprint_step")
            else:
                publish_board_update(source="sprint_step")
        if single_step:
            if active_task and active_task.get("id") and handler not in ("idle", "needs_user", "blocked"):
                _record_last_step_outcome(
                    str(active_task["id"]),
                    lane_before or get_task_lane(str(active_task["id"])) or "",
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


def run_in_progress_step(
    brief: str,
    ollama_url: str,
    task_id: Optional[str] = None,
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
            sorted_tasks = sorted(
                in_progress,
                key=lambda t: (t.get("priority") if isinstance(t.get("priority"), (int, float)) else 100, str(t.get("id", ""))),
            )
            active_task = dict(sorted_tasks[0])
        else:
            raise ValueError("No cards in In Progress")

    if not active_task:
        raise ValueError("In Progress task not found")

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
        state.DEV_STEP_INTERRUPTED = True
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
            save_current_project_state()
            publish_board_delta(tid, source="sprint_step")
        _record_last_step_outcome(tid, lane_before, "Developer")
        _finish_single_step_progress(active_task)


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


def run_auto_sprint(brief: str, ollama_url: str, max_steps: int | None = None) -> Dict[str, Any]:
    import time

    from backend.services.backlog_preflight import log_backlog_preflight_warnings
    from backend.services.sprint_session import set_sprint_mode

    set_sprint_mode("auto")
    state.SPRINT_CANCEL = False
    state.SPRINT_CANCEL_INTENT = None
    state.SPRINT_NEEDS_USER_COUNT = 0
    brief = resolve_brief_for_sprint(brief)
    log_backlog_preflight_warnings()
    ws = get_workflow_settings()
    limit = max_steps if max_steps is not None else int(ws.get("maxSprintSteps", 20))
    state.SPRINT_PROGRESS_MAX = limit
    steps = 0
    status = "completed"
    session_start = time.monotonic()
    refresh_enabled = bool(ws.get("autoSprintSessionRefreshEnabled", True))
    refresh_minutes = int(ws.get("autoSprintSessionRefreshMinutes") or 60)
    refresh_sec = max(60, refresh_minutes * 60)

    while steps < limit and not state.SPRINT_CANCEL:
        with state.STATE_LOCK:
            if not has_sprint_work():
                status = "idle"
                break
        state.SPRINT_PROGRESS_STEP = steps + 1
        run_sprint_step(brief, ollama_url)
        steps += 1
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


def run_plan_and_run(brief: str, ollama_url: str, max_steps: int | None = None) -> Dict[str, Any]:
    ws = get_workflow_settings()
    limit = max_steps if max_steps is not None else int(ws.get("maxSprintSteps", 20))
    state.SPRINT_CANCEL = False
    state.SPRINT_CANCEL_INTENT = None
    state.SPRINT_NEEDS_USER_COUNT = 0
    state.SPRINT_PROGRESS_MAX = limit
    state.SPRINT_PROGRESS_STEP = 0

    publish_sprint_progress(
        phase="po_plan",
        step=0,
        max_steps=limit,
        agent="Product Owner",
        task_id=PLANNING_TASK_ID,
        task_title="Plan & Run started",
        lane="Backlog",
    )
    add_system_log("System", "info", "Plan & Run started — PO planning, then sprint steps…")

    run_po_plan(brief, ollama_url)

    if state.SPRINT_CANCEL:
        summary = _build_sprint_summary(0, "cancelled")
        publish_sprint_progress(
            phase="cancelled",
            step=0,
            max_steps=limit,
            agent="System",
            task_title="Plan & Run cancelled",
        )
        return summary

    summary = run_auto_sprint(brief, ollama_url, max_steps=max_steps)
    publish_sprint_progress(
        phase="done",
        step=int(summary.get("stepsRun", 0)),
        max_steps=limit,
        agent="System",
        task_title=f"Plan & Run finished ({summary.get('status', 'completed')})",
    )
    return summary
