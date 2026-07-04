import json
import os
import random
import re
import uuid
from typing import Any, Dict, List, Optional

from backend import state
from backend.agents.registry import agent_cr, agent_dev, agent_po, agent_qa
from backend.agents.task_context import (
    apply_po_clarification,
    build_dod_block,
    build_task_prompt,
    clear_active_sprint_context,
    clear_qa_failure,
    init_new_task,
    next_claimable_backlog_task,
    normalize_task,
    record_task_decision,
    set_active_sprint_context,
    set_qa_failure,
    sort_backlog,
    task_dependencies_met,
)
from backend.services.board_lanes import normalize_board_lanes
from backend.services.board_service import move_board_stage
from backend.services.brief_service import (
    append_brief_text,
    append_feature_to_brief,
    existing_backlog_titles,
    record_brief_changelog,
    set_project_brief,
)
from backend.services.events import publish_event
from backend.services.git_service import git_commit, git_init
from backend.services.logs import add_system_log
from backend.services.project_service import save_current_project_state
from backend.services.workflow_settings import (
    get_active_lanes,
    get_last_sprint_summary,
    get_workflow_settings,
    save_sprint_summary,
)
from backend.workspace.files import write_workspace_file


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
    return task_id in [t["id"] for t in state.SHARED_BOARD.get(lane, [])]


def _dev_needs_po(result: str) -> bool:
    lower = result.lower()
    return any(
        m in lower
        for m in (
            "needs po",
            "need po",
            "needs product owner",
            "need clarification",
            "unclear requirement",
            "escalate to po",
            "move the task to 'needs po'",
            "blocked on requirements",
        )
    )


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


def _qa_failed(result: str) -> bool:
    lower = result.lower()
    return any(m in lower for m in ("fail", "failed", "rejected", "does not pass", "not pass"))


def _enrich_task_from_po(raw: Dict[str, Any]) -> Dict[str, Any]:
    task = dict(raw)
    if "acceptanceCriteria" not in task and "acceptance_criteria" in task:
        task["acceptanceCriteria"] = task.pop("acceptance_criteria")
    if not isinstance(task.get("acceptanceCriteria"), list):
        task["acceptanceCriteria"] = []
    if "blockedBy" not in task and "blocked_by" in task:
        task["blockedBy"] = task.pop("blocked_by")
    if not isinstance(task.get("blockedBy"), list):
        task["blockedBy"] = []
    task.setdefault("priority", 100)
    return task


def _new_task_lane() -> str:
    ws = get_workflow_settings()
    return "Pending Approval" if ws.get("requireBacklogApproval") else "Backlog"


def _append_tasks(tasks: List[Dict[str, Any]]) -> int:
    lane = _new_task_lane()
    state.SHARED_BOARD.setdefault(lane, [])
    added = 0
    for raw in tasks:
        task = _enrich_task_from_po(raw)
        if "id" not in task:
            task["id"] = "TASK-" + str(uuid.uuid4())[:8].upper()
        init_new_task(task)
        task["status"] = lane
        state.SHARED_BOARD[lane].append(task)
        added += 1
    if lane == "Backlog":
        sort_backlog()
    return added


def _dev_complete_lane() -> str:
    return "Code Review" if get_workflow_settings().get("requireCodeReview") else "QA"


def _llm_iterations() -> int:
    return int(get_workflow_settings().get("maxLlmIterationsPerStep", 8))


def _commit_on_done(task: Dict[str, Any]) -> None:
    ws_dir = state.WORKSPACE_DIR
    if not os.path.isdir(os.path.join(ws_dir, ".git")):
        git_init()
    msg = f"{task['id']}: {task['title']}"
    result = git_commit(msg)
    if result.get("success"):
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
    set_project_brief(brief, source="user")
    agent_po.ollama_url = ollama_url
    normalize_board_lanes(state.SHARED_BOARD)
    existing = existing_backlog_titles()
    existing_hint = ", ".join(existing) if existing else "(none yet)"
    add_system_log("Product Owner", "info", "Decomposing project brief into features…")

    po_output = agent_po.execute_step(
        "You are the Product Owner. Decompose the project brief into developer-ready features. "
        "Reply with ONLY a JSON array. Each object must have: id, title, description, "
        "acceptanceCriteria (string array), optional blockedBy (task id array), optional priority (number, lower=higher).\n"
        f"Existing titles (do NOT duplicate): {existing_hint}\n"
        f"{build_dod_block()}\nProject brief:\n{brief}",
        max_iterations=_llm_iterations(),
    )

    if po_output == "SIMULATION_FALLBACK":
        tasks = [
            {"title": "Create core scaffold", "description": "Primary module structure.", "acceptanceCriteria": ["Entry point runs"]},
            {"title": "Implement main feature", "description": "Deliver brief capability.", "acceptanceCriteria": ["Feature works end-to-end"]},
        ]
        count = _append_tasks(tasks)
        add_system_log("Product Owner", "success", f"Added {count} feature(s) (offline).")
    else:
        try:
            parsed = extract_json_array_from_text(po_output)
            new_tasks = [t for t in parsed if t.get("title") not in existing]
            count = _append_tasks(new_tasks)
            add_system_log("Product Owner", "success", f"PO created {count} new feature(s).")
        except (ValueError, json.JSONDecodeError) as e:
            add_system_log("Product Owner", "error", f"Failed to parse PO output: {e}")


def run_po_add_feature(title: str, description: str, ollama_url: str) -> None:
    append_feature_to_brief(title, description, source="user")
    agent_po.ollama_url = ollama_url
    normalize_board_lanes(state.SHARED_BOARD)
    add_system_log("Product Owner", "info", f"Refining feature '{title}'…")

    po_output = agent_po.execute_step(
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


def _apply_po_clarification_result(active_task: Dict[str, Any], result: str) -> None:
    obj = extract_json_object_from_text(result)
    if obj:
        apply_po_clarification(
            active_task["id"],
            description=obj.get("description"),
            acceptance_criteria=obj.get("acceptanceCriteria") or obj.get("acceptance_criteria"),
        )
        addition = obj.get("briefAddition") or obj.get("brief_addition") or ""
        if addition:
            append_brief_text(addition, "po", f"PO clarification for {active_task['id']}")
    record_brief_changelog("po", f"Clarified {active_task['title']}", result[:300])


def _run_po_clarification(active_task: Dict[str, Any], brief: str) -> None:
    set_active_sprint_context(active_task["id"], "Product Owner")
    add_system_log("Product Owner", "info", f"Clarifying '{active_task['title']}'…")
    prompt = (
        build_task_prompt(active_task, brief)
        + "\nDeveloper needs clarification. Reply with a JSON object: "
        '{"description": "...", "acceptanceCriteria": ["..."], "briefAddition": "..."}\n'
        "Then use update_board to move back to 'In Progress'."
    )
    result = agent_po.execute_step(prompt, max_iterations=_llm_iterations())
    if result == "SIMULATION_FALLBACK":
        record_task_decision(active_task["id"], "Product Owner", "clarification", "Offline clarification")
    else:
        record_task_decision(active_task["id"], "Product Owner", "clarification", result[:500], result)
        _apply_po_clarification_result(active_task, result)
    if _task_in_lane(active_task["id"], "Needs PO"):
        move_board_stage(active_task["id"], "In Progress")


def _run_developer_step(active_task: Dict[str, Any], brief: str) -> None:
    set_active_sprint_context(active_task["id"], "Developer")
    add_system_log("Developer", "info", f"Implementing '{active_task['title']}'…")
    target = _dev_complete_lane()
    prompt = (
        build_task_prompt(active_task, brief)
        + "\nImplement using write_file/read_file. "
        "Unclear requirements → move to 'Needs PO'. "
        "User-only decisions (keys, design) → move to 'Needs User' and state userQuestion. "
        f"When complete → move to '{target}'."
    )
    result = agent_dev.execute_step(prompt, max_iterations=_llm_iterations())
    if result == "SIMULATION_FALLBACK":
        _simulate_dev_work(active_task)
        return
    record_task_decision(active_task["id"], "Developer", "work", result[:500], result)
    if _task_in_lane(active_task["id"], "In Progress"):
        if _dev_needs_user(result):
            active_task["userQuestion"] = result[:500]
            move_board_stage(active_task["id"], "Needs User")
        elif _dev_needs_po(result):
            move_board_stage(active_task["id"], "Needs PO")
        else:
            clear_qa_failure(active_task["id"])
            move_board_stage(active_task["id"], target)


def _run_code_review_step(active_task: Dict[str, Any], brief: str) -> None:
    set_active_sprint_context(active_task["id"], "Code Reviewer")
    add_system_log("Code Reviewer", "info", f"Reviewing '{active_task['title']}'…")
    prompt = (
        build_task_prompt(active_task, brief)
        + "\nReview with read_file. Pass → 'QA'. Fail → 'In Progress'."
    )
    result = agent_cr.execute_step(prompt, max_iterations=_llm_iterations())
    if result == "SIMULATION_FALLBACK":
        _simulate_code_review(active_task)
        return
    record_task_decision(active_task["id"], "Code Reviewer", "review", result[:500], result)
    if _task_in_lane(active_task["id"], "Code Review"):
        move_board_stage(active_task["id"], "QA")


def _run_qa_step(active_task: Dict[str, Any], brief: str) -> None:
    set_active_sprint_context(active_task["id"], "QA Tester")
    add_system_log("QA Tester", "info", f"Validating '{active_task['title']}'…")
    ac = active_task.get("acceptanceCriteria") or []
    ac_block = "\n".join(f"- {c}" for c in ac) if ac else "(see description)"
    prompt = (
        build_task_prompt(active_task, brief)
        + f"\nValidate acceptance criteria:\n{ac_block}\n"
        + f"{build_dod_block()}"
        "Use read_file and run_test. Pass → 'Done'. Fail → 'In Progress' with failure details."
    )
    result = agent_qa.execute_step(prompt, max_iterations=_llm_iterations())
    if result == "SIMULATION_FALLBACK":
        _simulate_qa(active_task)
        return
    record_task_decision(active_task["id"], "QA Tester", "qa", result[:500], result)
    if _task_in_lane(active_task["id"], "QA"):
        if _qa_failed(result):
            set_qa_failure(active_task["id"], result[:500], result)
            record_task_decision(active_task["id"], "QA Tester", "qa_fail", result[:500], result)
            move_board_stage(active_task["id"], "In Progress")
        else:
            move_board_stage(active_task["id"], "Done")
            _commit_on_done(active_task)


def _sprint_lanes_active() -> List[str]:
    ws = get_workflow_settings()
    lanes = ["Needs PO", "In Progress", "Backlog", "QA"]
    if ws.get("requireCodeReview"):
        lanes.insert(3, "Code Review")
    return lanes


def run_sprint_step(brief: str, ollama_url: str) -> None:
    set_project_brief(brief, source="user")
    agent_dev.ollama_url = ollama_url
    agent_po.ollama_url = ollama_url
    agent_qa.ollama_url = ollama_url
    agent_cr.ollama_url = ollama_url
    normalize_board_lanes(state.SHARED_BOARD)

    try:
        if state.SHARED_BOARD.get("Needs PO"):
            _run_po_clarification(state.SHARED_BOARD["Needs PO"][0], brief)
        elif state.SHARED_BOARD.get("Needs User"):
            add_system_log("System", "info", "Feature waiting in Needs User — resolve via UI.")
        elif state.SHARED_BOARD.get("In Progress"):
            _run_developer_step(state.SHARED_BOARD["In Progress"][0], brief)
        elif state.SHARED_BOARD.get("Backlog"):
            task = next_claimable_backlog_task()
            if task:
                move_board_stage(task["id"], "In Progress")
                record_task_decision(task["id"], "Developer", "claim", "Claimed from Backlog")
            else:
                blocked = [t for t in state.SHARED_BOARD["Backlog"] if not task_dependencies_met(t)]
                if blocked:
                    add_system_log("System", "info", f"Backlog blocked — waiting on dependencies for {blocked[0]['id']}")
        elif get_workflow_settings().get("requireCodeReview") and state.SHARED_BOARD.get("Code Review"):
            _run_code_review_step(state.SHARED_BOARD["Code Review"][0], brief)
        elif state.SHARED_BOARD.get("QA"):
            _run_qa_step(state.SHARED_BOARD["QA"][0], brief)
        else:
            add_system_log("System", "warning", "No active features. Send brief to PO or add a feature.")
    finally:
        clear_active_sprint_context()
    save_current_project_state()


def _build_sprint_summary(steps: int) -> Dict[str, Any]:
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
    }
    save_sprint_summary(summary)
    publish_event("sprint", summary)
    return summary


def run_auto_sprint(brief: str, ollama_url: str, max_steps: int | None = None) -> Dict[str, Any]:
    state.SPRINT_CANCEL = False
    ws = get_workflow_settings()
    limit = max_steps if max_steps is not None else int(ws.get("maxSprintSteps", 20))
    steps = 0
    lanes = _sprint_lanes_active()
    while steps < limit and not state.SPRINT_CANCEL:
        if not any(len(state.SHARED_BOARD.get(l, [])) > 0 for l in lanes):
            break
        run_sprint_step(brief, ollama_url)
        steps += 1
    if state.SPRINT_CANCEL:
        add_system_log("System", "info", "Auto sprint cancelled.")
    else:
        add_system_log("System", "info", f"Auto sprint finished after {steps} step(s).")
    return _build_sprint_summary(steps)


def run_plan_and_run(brief: str, ollama_url: str, max_steps: int | None = None) -> Dict[str, Any]:
    run_po_plan(brief, ollama_url)
    return run_auto_sprint(brief, ollama_url, max_steps=max_steps)
