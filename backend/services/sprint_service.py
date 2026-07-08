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
    find_task_by_id,
    get_task_lane,
    increment_po_round_trips,
    init_new_task,
    create_spike_task,
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
    PO_SMALLEST_TASKS_GUIDANCE,
    append_feature_to_brief,
    append_brief_text,
    existing_backlog_titles,
    record_brief_changelog,
    set_project_brief,
)
from backend.services.events import publish_event
from backend.services.feature_similarity import iter_board_tasks, link_related_features
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
    "Pre-loaded file content may be stale or truncated — always call read_file immediately "
    "before apply_patch on a path, even when that file appears above."
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


def _prepare_single_step_progress() -> bool:
    """Configure progress for one manual step (Execute Step / Run In Progress)."""
    if state.SPRINT_PROGRESS_STEP > 0 and state.SPRINT_PROGRESS_MAX > 1:
        return False
    state.LAST_STEP_OUTCOME = None
    state.LAST_AGENT_STEP_RESULT = None
    state.SPRINT_PROGRESS_MAX = 1
    state.SPRINT_PROGRESS_STEP = 1
    return True


def _build_last_step_outcome(
    task_id: str,
    lane_before: str,
    agent: str,
    *,
    agent_result: Optional[str] = None,
) -> Dict[str, Any]:
    task = find_task_by_id(task_id)
    lane_after = get_task_lane(task_id) or lane_before
    tool_failures = _count_task_tool_failures(task) if task else 0
    title = str(task.get("title", task_id)) if task else task_id
    ok = True
    message = f"Step completed on '{title}'."
    if agent_result:
        if agent_result == "SIMULATION_FALLBACK":
            ok = False
            message = f"Ollama unavailable — simulation fallback on '{title}'."
        elif agent_result.startswith("Stopped:") or agent_result.startswith("Max tool iterations"):
            ok = False
            message = f"{agent_result[:200]}"
    if tool_failures > 0:
        ok = False
        message = (
            f"Step finished with {tool_failures} tool failure(s) on '{title}'. "
            f"Card still in {lane_after}. Open the card → Transcript or Tools tab."
        )
    elif lane_before != lane_after:
        message = f"'{title}' moved from {lane_before} → {lane_after}."
    elif lane_before == lane_after and lane_after == "In Progress":
        message = f"Dev step finished on '{title}' — card still In Progress."
    return {
        "taskId": task_id,
        "agent": agent,
        "laneBefore": lane_before,
        "laneAfter": lane_after,
        "toolFailures": tool_failures,
        "ok": ok,
        "message": message,
    }


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
    return True


def _check_stuck_and_escalate(task_id: str, lane_before: str) -> None:
    """Escalate when a sprint step completes without moving the card."""
    task = find_task_by_id(task_id)
    if not task:
        return
    normalize_task(task)
    lane_after = get_task_lane(task_id) or lane_before

    if lane_before != lane_after:
        task["stuckLoops"] = 0
        return

    task["stuckLoops"] = int(task.get("stuckLoops", 0)) + 1
    ws = get_workflow_settings()
    max_stuck = int(ws.get("maxStuckSteps", 3))
    if task["stuckLoops"] < max_stuck:
        return

    task["stuckLoops"] = 0
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


def _mark_sprint_step_start() -> str:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    state.SPRINT_STEP_STARTED_AT = ts
    state.STEP_FILE_READS.clear()
    state.STEP_PATCH_FAILURES.clear()
    from backend.services.tool_cache import clear_tool_cache

    clear_tool_cache()
    return ts


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


def _inject_sprint_context(
    active_task: Dict[str, Any],
    brief: str,
    agent_role: str,
    instructions: str,
) -> str:
    """Build sprint prompt with pre-loaded file contents (no Tools log row per step)."""
    from backend.services.prompt_budget import (
        semantic_sprint_context_max_chars,
        sprint_file_context_max_chars,
    )
    from backend.services.workflow_settings import get_workflow_settings
    from backend.storage.code_index import build_semantic_sprint_context

    task_id = active_task["id"]
    num_ctx = int(get_workflow_settings().get("ollamaNumCtx", 32768))
    total_budget = sprint_file_context_max_chars(num_ctx)
    semantic_block, sem_paths = build_semantic_sprint_context(
        active_task,
        max_chars=semantic_sprint_context_max_chars(num_ctx),
    )
    graph_block = ""
    try:
        from backend.services.graphify_service import build_graphify_sprint_context, graphify_status

        if graphify_status().get("available") or graphify_status().get("reportExists"):
            graph_block = build_graphify_sprint_context(
                active_task,
                max_chars=min(2500, semantic_sprint_context_max_chars(num_ctx) // 2),
            )
    except Exception:
        graph_block = ""
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
    base = build_task_prompt(active_task, brief)
    parts = [base]
    if context_block:
        parts.append(context_block)
        parts.append(CONTEXT_INJECT_NOTE)
    parts.append(instructions)
    return "\n".join(parts)


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
    """Block dev board advance when subtasks pending or lint unresolved."""
    from backend.services.subtask_service import subtask_gate_blocks_advance

    blocked, reason = subtask_gate_blocks_advance(task)
    if blocked:
        return blocked, reason
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
    files = task.get("files") or []
    if _task_has_work_files(task):
        return
    failures = _count_task_tool_failures(task)
    title = task.get("title", task_id)
    if failures:
        add_system_log(
            "Developer",
            "warning",
            f"Developer advanced '{title}' with no files recorded — "
            f"{failures} failed tool(s) in transcript (open task → Transcript, red entries)",
        )
    else:
        add_system_log(
            "Developer",
            "warning",
            f"Developer advanced '{title}' with no files recorded — "
            "no write/read tools logged; open task → Transcript and Agent Decisions",
        )


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


def _simulate_dev_work(active_task: Dict[str, Any]) -> None:
    title = active_task["title"].lower()
    if "meal" in title or "recipe" in title or "api" in title:
        file_name, content = "meal_service.js", "module.exports = function fetchMealsQuery(q) { return { success: true, meals: [] }; };"
    elif "auth" in title or "secure" in title:
        file_name, content = "auth.js", "module.exports = function authenticateUser(u) { return 'token'; };"
    else:
        file_name, content = "index.js", "function init() { console.log('init'); }\ninit();"
    write_workspace_file(file_name, content)
    clear_qa_failure(active_task["id"])
    move_board_stage(active_task["id"], _dev_complete_lane())
    record_task_decision(active_task["id"], "Developer", "completion", f"Offline fallback wrote {file_name}")


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
    """Parse PO JSON array output and append new backlog tasks."""
    if po_output == "SIMULATION_FALLBACK":
        tasks = [
            {"title": "Create core scaffold", "description": "Primary module structure.", "acceptanceCriteria": ["Entry point runs"]},
            {"title": "Implement main feature", "description": "Deliver brief capability.", "acceptanceCriteria": ["Feature works end-to-end"]},
        ]
        count = _append_tasks(tasks)
        add_system_log("Product Owner", "success", f"Added {count} feature(s) (offline).")
        return count
    if not po_output:
        return 0
    try:
        parsed = extract_json_array_from_text(po_output)
        new_tasks = [t for t in parsed if t.get("title") not in existing]
        count = _append_tasks(new_tasks)
        add_system_log("Product Owner", "success", f"PO created {count} new feature(s).")
        return count
    except (ValueError, json.JSONDecodeError) as e:
        add_system_log("Product Owner", "error", f"Failed to parse PO output: {e}")
        return 0


def run_po_plan_outline(brief: str, ollama_url: str) -> str:
    """Generate a markdown plan outline (phase 1) without creating backlog cards."""
    from backend.services.events import publish_event
    from backend.services.prompt_budget import truncate_brief

    set_project_brief(brief, source="user")
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
            "You are the Product Owner. Produce a concise markdown project plan ONLY — no JSON, no code.\n"
            "Use these sections:\n"
            "## Summary\n## Approach\n## Risks\n## Open questions\n## Proposed epics\n"
            f"{build_dod_block()}\nProject brief:\n{brief_text}",
            max_iterations=1,
        )
    finally:
        clear_active_sprint_context()

    if outline == "SIMULATION_FALLBACK":
        outline = (
            "## Summary\nOffline plan stub.\n\n## Approach\nScaffold core modules first.\n\n"
            "## Risks\nUnknown integration points.\n\n## Open questions\n(none)\n\n"
            "## Proposed epics\n- Core scaffold\n- Main feature\n"
        )

    state.PROJECT_PLAN_OUTLINE = outline
    for block in outline.split("\n\n"):
        stripped = block.strip()
        if stripped:
            publish_event("plan_chunk", {"chunk": stripped + "\n\n"})
    publish_event("plan_chunk", {"phase": "done", "outline": outline})
    add_system_log("Product Owner", "success", "Plan outline ready — review before generating backlog.")
    save_current_project_state()
    return outline


def run_po_plan_backlog(brief: str, ollama_url: str, outline: Optional[str] = None) -> int:
    """Convert an approved plan outline into backlog JSON tasks (phase 2)."""
    set_project_brief(brief, source="user")
    agent_po.ollama_url = ollama_url
    normalize_board_lanes(state.SHARED_BOARD)
    existing = existing_backlog_titles()
    existing_set = set(existing)
    existing_hint = ", ".join(existing) if existing else "(none yet)"
    outline_text = coerce_task_text(outline or state.PROJECT_PLAN_OUTLINE or "").strip()
    if not outline_text:
        add_system_log("Product Owner", "warning", "No plan outline — run Plan outline first.")
        return 0

    add_system_log("Product Owner", "info", "Generating backlog from approved plan outline…")
    po_output = ""
    try:
        set_active_sprint_context(PLANNING_TASK_ID, "Product Owner")
        po_output = agent_po.execute_step(
            f"{PO_SMALLEST_TASKS_GUIDANCE}\n\n"
            "You are the Product Owner. Convert the approved plan outline into developer-ready backlog cards.\n"
            "Reply with ONLY a JSON array. Each object must have: title, description, "
            "acceptanceCriteria (string array), optional blockedBy, optional priority.\n"
            f"Existing titles (do NOT duplicate): {existing_hint}\n"
            f"{build_dod_block()}\nApproved plan outline:\n{outline_text}\n\n"
            f"Project brief (context):\n{brief}",
            max_iterations=1,
        )
    finally:
        clear_active_sprint_context()

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

    set_project_brief(brief, source="user")
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
        task_title="Decomposing brief into backlog…",
        lane="Backlog",
    )
    add_system_log("Product Owner", "info", "Decomposing project brief into features…")

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
            f"{PO_SMALLEST_TASKS_GUIDANCE}\n\n"
            "You are the Product Owner. Decompose the project brief into developer-ready features. "
            "Reply with ONLY a JSON array. Each object must have: title, description, "
            "acceptanceCriteria (string array), optional id (hint only — the system assigns TASK-{GUID}), "
            "optional blockedBy (array of id values from the same JSON array), optional priority (number, lower=higher).\n"
            f"Existing titles (do NOT duplicate): {existing_hint}\n"
            f"{build_dod_block()}\nProject brief:\n{brief}",
            max_iterations=_llm_iterations(),
        )
        add_system_log("Product Owner", "info", "PO received response, parsing backlog…")
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
        tasks = [
            {"title": "Create core scaffold", "description": "Primary module structure.", "acceptanceCriteria": ["Entry point runs"]},
            {"title": "Implement main feature", "description": "Deliver brief capability.", "acceptanceCriteria": ["Feature works end-to-end"]},
        ]
        count = _append_tasks(tasks)
        add_system_log("Product Owner", "success", f"Added {count} feature(s) (offline).")
    elif po_output:
        try:
            parsed = extract_json_array_from_text(po_output)
            new_tasks = [t for t in parsed if t.get("title") not in existing]
            count = _append_tasks(new_tasks)
            add_system_log("Product Owner", "success", f"PO created {count} new feature(s).")
        except (ValueError, json.JSONDecodeError) as e:
            add_system_log("Product Owner", "error", f"Failed to parse PO output: {e}")

    publish_sprint_progress(
        phase="po_plan",
        step=0,
        max_steps=max_steps,
        agent="Product Owner",
        task_id=PLANNING_TASK_ID,
        task_title="PO plan complete",
        lane="Backlog",
    )


def run_po_add_feature(title: str, description: str, ollama_url: str) -> None:
    append_feature_to_brief(title, description, source="user")
    agent_po.ollama_url = ollama_url
    normalize_board_lanes(state.SHARED_BOARD)
    add_system_log("Product Owner", "info", f"Refining feature '{title}'…")

    po_output = agent_po.execute_step(
        f"{PO_SMALLEST_TASKS_GUIDANCE}\n\n"
        "Reply with ONLY a JSON array with ONE object: id, title, description, acceptanceCriteria, "
        "optional blockedBy, optional priority.\n\n"
        f"Brief:\n{state.PROJECT_BRIEF}\n\nFeature:\n{title}: {description}",
        max_iterations=_llm_iterations(),
    )

    if po_output == "SIMULATION_FALLBACK":
        _append_tasks([{"title": title, "description": description, "acceptanceCriteria": [description]}])
    else:
        try:
            parsed = extract_json_array_from_text(po_output)
            if parsed:
                _append_tasks(parsed[:1])
            else:
                _append_tasks([{"title": title, "description": description}])
        except (ValueError, json.JSONDecodeError):
            _append_tasks([{"title": title, "description": description}])

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
            f"{PO_SMALLEST_TASKS_GUIDANCE}\n\n{prompt}\n\n"
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
        else:
            added = apply_backlog_from_po_response(
                json.dumps(
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
                ),
                task_id,
            )
            backlog_after = state.SHARED_BOARD.get("Backlog", [])
            new_task_ids = [t["id"] for t in backlog_after if t["id"] not in backlog_before_ids]

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
        "Reply with a JSON object:\n"
        '{"ready": true} when acceptance criteria are sufficient to implement, OR\n'
        '{"ready": false, "questions": ["..."], "explorationNotes": "..."} when clarification is needed, OR\n'
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
        _check_stuck_and_escalate(task_id, lane_before)


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
        _check_stuck_and_escalate(task_id, lane_before)


def _run_refinement_po_update(active_task: Dict[str, Any], brief: str) -> None:
    task_id = active_task["id"]
    lane_before = get_task_lane(task_id) or "Refinement"
    set_active_sprint_context(task_id, "Product Owner")
    add_system_log("Product Owner", "info", f"Refinement update for '{active_task['title']}'…")
    questions = active_task.get("refinementQuestions") or []
    q_block = "\n".join(f"- {q}" for q in questions) if questions else "(none listed)"
    dev_ready = bool(active_task.get("refinementDevReady"))
    prompt = (
        build_task_prompt(active_task, brief)
        + f"\nDeveloper refinement questions:\n{q_block}\n"
        + f"Developer marked ready: {dev_ready}\n"
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
        if result == "SIMULATION_FALLBACK":
            clarified = True
        else:
            record_task_decision(task_id, "Product Owner", "refinement_po", result[:500], result)
            clarified = _apply_po_clarification_result(task, result)
            obj = extract_json_object_from_text(result)
            if obj and isinstance(obj.get("executionPlan"), list):
                from backend.services.subtask_service import apply_execution_plan

                apply_execution_plan(task_id, obj["executionPlan"])
        task["refinementStatus"] = "po_updated"
        if not clarified:
            add_system_log(
                "Product Owner",
                "warning",
                f"Refinement PO update incomplete for '{task['title']}'",
            )
        if _task_in_lane(task_id, "Refinement") and task.get("refinementDevReady"):
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
        elif _task_in_lane(task_id, "Backlog") and task.get("refinementComplete"):
            publish_activity(
                task_id,
                "refinement_complete",
                "PO marked refinement complete",
                role="assistant",
                agent="Product Owner",
                lane="Backlog",
            )
        _check_stuck_and_escalate(task_id, lane_before)


def _run_po_clarification(active_task: Dict[str, Any], brief: str) -> None:
    task_id = active_task["id"]
    lane_before = get_task_lane(task_id) or "Needs PO"
    set_active_sprint_context(task_id, "Product Owner")
    add_system_log("Product Owner", "info", f"Clarifying '{active_task['title']}'…")
    prompt = (
        build_task_prompt(active_task, brief)
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
        if result == "SIMULATION_FALLBACK":
            record_task_decision(task_id, "Product Owner", "clarification", "Offline clarification")
            clarified = True
        else:
            record_task_decision(task_id, "Product Owner", "clarification", result[:500], result)
            clarified = _apply_po_clarification_result(task, result)
        if _task_in_lane(task_id, "Needs PO"):
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
        _check_stuck_and_escalate(task_id, lane_before)


def _run_developer_step(active_task: Dict[str, Any], brief: str) -> None:
    task_id = active_task["id"]
    lane_before = get_task_lane(task_id) or "In Progress"
    step_started = _mark_sprint_step_start()
    set_active_sprint_context(task_id, "Developer")
    add_system_log("Developer", "info", f"Implementing '{active_task['title']}'…")
    target = _dev_complete_lane()
    lint_cmd = derive_project_lint_command()
    lint_hint = f" (e.g. '{lint_cmd}')" if lint_cmd else ""
    instructions = (
        "Registered tools: read_file, write_file, apply_patch, run_command, update_board, "
        "grep, glob_file_search, git_status, git_diff, git_commit, search_code. "
        "Use apply_patch for edits to existing files; write_file for new files. "
        "Before apply_patch you must read_file on the same path in this step and copy old_text "
        "verbatim from that read_file result — never from pre-loaded context or analyze output. "
        "Implement using apply_patch and write_file. "
        "Read each tool result before calling update_board — if write_file or apply_patch fails, "
        "try a different path or approach (do not repeat the same failing arguments). "
        f"Use run_command with the project lint command{lint_hint}. "
        "Findings are expected — fix each file:line listed in the Problems section, "
        "don't treat lint output as a tool failure. "
        "Fix syntax/parse errors before logic changes. "
        "After edits, run the lint command once to verify. "
        "Do NOT re-run the same lint command without fixing code first. "
        "Unclear requirements → move to 'Needs PO' (not Needs User). "
        "Needs User ONLY for: secrets/credentials you cannot invent, irreversible external "
        "actions (production deploy, billing), or product choices with no default in brief/AC. "
        "Set a specific userQuestion when moving to Needs User. "
        "Do NOT move to Needs User for lint errors, missing files, or implementation questions. "
        f"When complete and files are written → move to '{target}'."
        f"{_autonomous_instruction_suffix()}"
    )
    prompt = _inject_sprint_context(active_task, brief, "Developer", instructions)
    from backend.services.fix_verify_loop import run_fix_verify_loop

    result = run_fix_verify_loop(
        agent_dev,
        active_task,
        prompt,
        max_iterations=_llm_iterations(),
    )
    state.LAST_AGENT_STEP_RESULT = result

    with state.STATE_LOCK:
        task = find_task_by_id(task_id)
        if not task:
            return
        if result == "SIMULATION_FALLBACK":
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
                    if fresh and _task_has_work_files(fresh):
                        blocked, reason = dev_gate_blocks_advance(fresh)
                        if blocked:
                            add_system_log("Developer", "warning", f"{task_id}: {reason}")
                        else:
                            clear_qa_failure(task_id)
                            move_board_stage(task_id, target)
                    elif fresh:
                        add_system_log(
                            "Developer",
                            "warning",
                            f"'{fresh.get('title', task_id)}' finished with no files — staying In Progress",
                        )
        _log_sprint_step_outcome("Developer", task_id, task.get("title", task_id), lane_before, result)
        _audit_dev_files_written(find_task_by_id(task_id) or task, lane_before, task_id)
        _audit_dev_verification(find_task_by_id(task_id) or task, lane_before, task_id, step_started)
        _check_stuck_and_escalate(task_id, lane_before)


def _run_code_review_step(active_task: Dict[str, Any], brief: str) -> None:
    task_id = active_task["id"]
    lane_before = get_task_lane(task_id) or "Code Review"
    _mark_sprint_step_start()
    set_active_sprint_context(task_id, "Code Reviewer")
    add_system_log("Code Reviewer", "info", f"Reviewing '{active_task['title']}'…")
    instructions = (
        "Registered tools: read_file, apply_patch, update_board, grep, glob_file_search, git_diff, search_code. "
        "Review with read_file. Pass → 'QA'. Fail → 'In Progress'."
    )
    prompt = _inject_sprint_context(active_task, brief, "Code Reviewer", instructions)
    result = agent_cr.execute_step(prompt, max_iterations=_llm_iterations())

    with state.STATE_LOCK:
        if not find_task_by_id(task_id):
            return
        if result == "SIMULATION_FALLBACK":
            task = find_task_by_id(task_id)
            if task:
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
        _check_stuck_and_escalate(task_id, lane_before)


def _run_qa_step(active_task: Dict[str, Any], brief: str) -> None:
    task_id = active_task["id"]
    lane_before = get_task_lane(task_id) or "QA"
    step_started = _mark_sprint_step_start()
    set_active_sprint_context(task_id, "QA Tester")
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
    instructions = (
        f"Validate acceptance criteria:\n{ac_block}\n"
        f"{build_dod_block()}"
        f"{_format_playbook_block(playbook)}"
        "Registered tools: read_file, run_test, run_command, update_board, grep, glob_file_search, search_code. "
        "Review the automated test results above. Use read_file and run_test for additional checks. "
        "Pass → 'Done'. Fail → 'In Progress' with failure details. "
        "You cannot move to Done without passing automated tests or successful run_test/run_command."
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
        if result == "SIMULATION_FALLBACK":
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
        _check_stuck_and_escalate(task_id, lane_before)


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
    return "idle", None


def _try_backlog_handler() -> tuple[Optional[str], Optional[Dict[str, Any]]]:
    """Return (handler, active_task) for Backlog lane work, or (None, None)."""
    if not state.SHARED_BOARD.get("Backlog"):
        return None, None
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
    blocked = [t for t in state.SHARED_BOARD["Backlog"] if not task_dependencies_met(t)]
    if blocked:
        return "blocked", None
    return "idle", None


def has_sprint_work() -> bool:
    """True when auto-sprint has actionable work in active lanes."""
    lanes = _sprint_lanes_active()
    return any(len(state.SHARED_BOARD.get(l, [])) > 0 for l in lanes)


def run_sprint_step(brief: str, ollama_url: str) -> None:
    set_project_brief(brief, source="user")
    agent_dev.ollama_url = ollama_url
    agent_po.ollama_url = ollama_url
    agent_qa.ollama_url = ollama_url
    agent_cr.ollama_url = ollama_url

    single_step = _prepare_single_step_progress()
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
            with state.STATE_LOCK:
                blocked = [t for t in state.SHARED_BOARD["Backlog"] if not task_dependencies_met(t)]
            if blocked:
                add_system_log("System", "info", f"Backlog blocked — waiting on dependencies for {blocked[0]['id']}")
        elif handler == "cr" and active_task:
            _run_code_review_step(active_task, brief)
        elif handler == "qa" and active_task:
            _run_qa_step(active_task, brief)
        elif handler == "idle":
            add_system_log("System", "warning", "No active features. Send brief to PO or add a feature.")
    finally:
        clear_active_sprint_context()
        state.SPRINT_STEP_STARTED_AT = None
        with state.STATE_LOCK:
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


def run_in_progress_step(
    brief: str,
    ollama_url: str,
    task_id: Optional[str] = None,
) -> None:
    """Run Dev on an In Progress card only — skips Needs PO, Backlog, and Refinement."""
    from backend.services.logs import add_system_log

    set_project_brief(brief, source="user")
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

    _prepare_single_step_progress()
    tid = str(active_task.get("id", ""))
    title = str(active_task.get("title", tid))
    lane_before = get_task_lane(tid) or "In Progress"
    add_system_log(
        "System",
        "info",
        f"Sprint handler: dev (in-progress-only) — '{title}' (In Progress)",
    )
    _emit_sprint_step_progress("dev", active_task)

    try:
        _run_developer_step(dict(active_task), brief)
    finally:
        clear_active_sprint_context()
        state.SPRINT_STEP_STARTED_AT = None
        with state.STATE_LOCK:
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
    save_sprint_summary(summary)
    publish_event("sprint", summary)
    return summary


def run_auto_sprint(brief: str, ollama_url: str, max_steps: int | None = None) -> Dict[str, Any]:
    state.SPRINT_CANCEL = False
    state.SPRINT_NEEDS_USER_COUNT = 0
    ws = get_workflow_settings()
    limit = max_steps if max_steps is not None else int(ws.get("maxSprintSteps", 20))
    state.SPRINT_PROGRESS_MAX = limit
    steps = 0
    status = "completed"

    while steps < limit and not state.SPRINT_CANCEL:
        with state.STATE_LOCK:
            if not has_sprint_work():
                status = "idle"
                break
        state.SPRINT_PROGRESS_STEP = steps + 1
        run_sprint_step(brief, ollama_url)
        steps += 1

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
    elif status == "idle":
        add_system_log("System", "info", "Auto sprint paused — no backlog work remaining.")
    else:
        add_system_log("System", "info", f"Auto sprint finished after {steps} step(s).")
    return _build_sprint_summary(steps, status)


def run_plan_and_run(brief: str, ollama_url: str, max_steps: int | None = None) -> Dict[str, Any]:
    ws = get_workflow_settings()
    limit = max_steps if max_steps is not None else int(ws.get("maxSprintSteps", 20))
    state.SPRINT_CANCEL = False
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

    with state.STATE_LOCK:
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
