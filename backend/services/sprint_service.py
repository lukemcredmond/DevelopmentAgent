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
    next_claimable_backlog_task,
    normalize_acceptance_criteria,
    normalize_task,
    publish_activity,
    record_task_decision,
    record_task_git_commit,
    set_active_sprint_context,
    set_qa_failure,
    sort_backlog,
    task_dependencies_met,
)
from backend.services.board_lanes import normalize_board_lanes
from backend.services.board_service import append_backlog_tasks, move_board_stage, publish_board_update
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
    step = state.SPRINT_PROGRESS_STEP or 0
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
    if int(task.get("poRoundTrips", 0)) >= max_po:
        msg = (
            f"Agents made no progress after {max_stuck} steps in '{lane_after}'. "
            "Please clarify requirements or make a decision."
        )
        task["userQuestion"] = msg
        move_board_stage(task_id, "Needs User")
        publish_activity(
            task_id,
            "stuck_loop",
            msg,
            role="system",
            agent="System",
            lane="Needs User",
        )
        add_system_log("System", "warning", f"{task_id}: stuck loop → Needs User")
    else:
        msg = (
            f"Agents made no progress after {max_stuck} steps in '{lane_after}' — "
            "escalating to PO for clarification."
        )
        increment_po_round_trips(task_id)
        move_board_stage(task_id, "Needs PO")
        publish_activity(
            task_id,
            "stuck_loop",
            msg,
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
    task["userQuestion"] = msg
    move_board_stage(task["id"], "Needs User")
    publish_activity(
        task["id"],
        "po_limit",
        msg,
        role="system",
        agent="System",
        lane="Needs User",
    )
    add_system_log("System", "warning", f"{task['id']}: {msg}")
    return True


def _dev_needs_user(result: str) -> bool:
    lower = result.lower()
    return any(
        m in lower
        for m in (
            "needs user",
            "need user",
            "user decision",
            "requires user input",
            "move the task to 'needs user'",
            "api key",
            "design choice",
        )
    )


def _mark_sprint_step_start() -> str:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    state.SPRINT_STEP_STARTED_AT = ts
    state.STEP_FILE_READS.clear()
    return ts


def _inject_sprint_context(
    active_task: Dict[str, Any],
    brief: str,
    agent_role: str,
    instructions: str,
) -> str:
    """Build sprint prompt with pre-loaded file contents and log context_inject event."""
    task_id = active_task["id"]
    context_block, paths = build_sprint_file_context(active_task)
    if paths:
        log_synthetic_tool_event(
            task_id,
            agent_role,
            "context_inject",
            tool_args={"paths": paths, "fileCount": len(paths)},
            tool_output=f"Pre-loaded {len(paths)} file(s): {', '.join(paths[:12])}"
            + ("…" if len(paths) > 12 else ""),
            success=True,
            source="context_inject",
        )
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
    from backend.agents.tool_outcomes import classify_run_command

    commands = derive_project_test_commands()
    results: List[Dict[str, Any]] = []
    all_passed = True

    for cmd in commands:
        output = run_agent_command(cmd)
        outcome = classify_run_command(cmd, output)
        tool_success = outcome != "execution_failed"
        if _playbook_item_failed(outcome):
            all_passed = False
        results.append(
            {
                "command": cmd,
                "success": outcome == "ok",
                "outcome": outcome,
                "output": output[:1500],
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


def _dev_has_verification(task: Dict[str, Any], step_started: str) -> bool:
    for entry in _transcript_entries_since(task, step_started):
        if entry.get("toolName") in ("run_test", "run_command") and entry.get("toolSuccess") is not False:
            return True
    return False


def _audit_dev_verification(task: Dict[str, Any], lane_before: str, task_id: str, step_started: str) -> None:
    if not get_workflow_settings().get("requireDevVerification"):
        return
    lane_after = get_task_lane(task_id) or lane_before
    target = _dev_complete_lane()
    if lane_before != "In Progress" or lane_after != target:
        return
    files = task.get("files") or []
    if not files:
        return
    if _dev_has_verification(task, step_started):
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
    if files:
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
    instructions = (
        "Registered tools: read_file, write_file, apply_patch, run_command, update_board, "
        "git_status, git_diff, git_commit, search_code. "
        "Use apply_patch for edits to existing files; write_file for new files. "
        "Before apply_patch you must read_file on the same path in this step and copy old_text "
        "verbatim from that read_file result — never from pre-loaded context or analyze output. "
        "Implement using apply_patch and write_file. "
        "Read each tool result before calling update_board — if write_file or apply_patch fails, "
        "try a different path or approach (do not repeat the same failing arguments). "
        "For Flutter/Dart use run_command with command 'flutter analyze'. "
        "If analyze returns issues (non-zero exit), fix them with apply_patch/write_file — "
        "do NOT re-run the same analyze command without fixing code first. "
        "Analyze findings are not a reason to move to Needs User. "
        "Unclear requirements → move to 'Needs PO'. "
        "User-only decisions (keys, design) → move to 'Needs User' with a specific userQuestion. "
        f"When complete and files are written → move to '{target}'."
    )
    prompt = _inject_sprint_context(active_task, brief, "Developer", instructions)
    result = agent_dev.execute_step(prompt, max_iterations=_llm_iterations())

    with state.STATE_LOCK:
        task = find_task_by_id(task_id)
        if not task:
            return
        if result == "SIMULATION_FALLBACK":
            _simulate_dev_work(task)
        else:
            record_task_decision(task_id, "Developer", "work", result[:500], result)
            if _task_in_lane(task_id, "In Progress"):
                if _dev_needs_user(result):
                    task["userQuestion"] = result[:500]
                    move_board_stage(task_id, "Needs User")
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
                    if fresh and fresh.get("files"):
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
        "Registered tools: read_file, apply_patch, update_board, git_diff, search_code. "
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
        "Registered tools: read_file, run_test, run_command, update_board, search_code. "
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
    lanes = ["Needs PO", "In Progress", "Backlog", "QA"]
    if ws.get("requireCodeReview"):
        lanes.insert(3, "Code Review")
    return lanes


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

    handler: Optional[str] = None
    active_task: Optional[Dict[str, Any]] = None

    with state.STATE_LOCK:
        normalize_board_lanes(state.SHARED_BOARD)
        if state.SHARED_BOARD.get("Needs PO"):
            active_task = dict(state.SHARED_BOARD["Needs PO"][0])
            handler = "po"
        elif state.SHARED_BOARD.get("Needs User"):
            handler = "needs_user"
        elif state.SHARED_BOARD.get("In Progress"):
            active_task = dict(state.SHARED_BOARD["In Progress"][0])
            handler = "dev"
        elif state.SHARED_BOARD.get("Backlog"):
            task = next_claimable_backlog_task()
            if task:
                move_board_stage(task["id"], "In Progress")
                record_task_decision(task["id"], "Developer", "claim", "Claimed from Backlog")
                active_task = dict(find_task_by_id(task["id"]) or task)
                handler = "dev"
            else:
                blocked = [t for t in state.SHARED_BOARD["Backlog"] if not task_dependencies_met(t)]
                if blocked:
                    handler = "blocked"
                else:
                    handler = "idle"
        elif get_workflow_settings().get("requireCodeReview") and state.SHARED_BOARD.get("Code Review"):
            active_task = dict(state.SHARED_BOARD["Code Review"][0])
            handler = "cr"
        elif state.SHARED_BOARD.get("QA"):
            active_task = dict(state.SHARED_BOARD["QA"][0])
            handler = "qa"
        else:
            handler = "idle"

    _emit_sprint_step_progress(handler or "idle", active_task)

    try:
        if handler == "po" and active_task:
            _run_po_clarification(active_task, brief)
        elif handler == "needs_user":
            add_system_log("System", "info", "Feature waiting in Needs User — resolve via UI.")
        elif handler == "dev" and active_task:
            _run_developer_step(active_task, brief)
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
            publish_board_update(source="sprint_step")


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
