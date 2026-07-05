import datetime
from typing import Any, Dict, List, Optional

from backend.config import MAX_TASK_DECISIONS
from backend import state
from backend.services.events import publish_event
from backend.services.workflow_settings import get_workflow_settings


def publish_activity(
    task_id: str,
    kind: str,
    content: str,
    *,
    role: str = "system",
    agent: Optional[str] = None,
    lane: Optional[str] = None,
) -> None:
    task = find_task_by_id(task_id)
    publish_event(
        "activity",
        {
            "taskId": task_id,
            "taskTitle": task.get("title", task_id) if task else task_id,
            "kind": kind,
            "role": role,
            "agent": agent or role,
            "content": content[:4000],
            "lane": lane,
            "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        },
    )


def find_task_by_id(task_id: str) -> Optional[Dict[str, Any]]:
    for tasks in state.SHARED_BOARD.values():
        for task in tasks:
            if task.get("id") == task_id:
                return task
    return None


def is_task_done(task_id: str) -> bool:
    return any(t.get("id") == task_id for t in state.SHARED_BOARD.get("Done", []))


def task_dependencies_met(task: Dict[str, Any]) -> bool:
    blocked_by = task.get("blockedBy") or []
    return all(is_task_done(dep_id) for dep_id in blocked_by)


def normalize_task(task: Dict[str, Any]) -> Dict[str, Any]:
    """Ensures task has context fields for file associations, decisions, and transcript."""
    if "files" not in task or not isinstance(task["files"], list):
        task["files"] = []
    if "decisions" not in task or not isinstance(task["decisions"], list):
        task["decisions"] = []
    if "transcript" not in task or not isinstance(task["transcript"], list):
        task["transcript"] = []
    if "acceptanceCriteria" not in task or not isinstance(task["acceptanceCriteria"], list):
        task["acceptanceCriteria"] = []
    if "blockedBy" not in task or not isinstance(task["blockedBy"], list):
        task["blockedBy"] = []
    if "priority" not in task or not isinstance(task["priority"], (int, float)):
        task["priority"] = 100
    if "qaFailure" not in task:
        task["qaFailure"] = None
    if "userQuestion" not in task:
        task["userQuestion"] = None
    if "poRoundTrips" not in task or not isinstance(task.get("poRoundTrips"), (int, float)):
        task["poRoundTrips"] = 0
    return task


def init_new_task(task: Dict[str, Any]) -> Dict[str, Any]:
    task.setdefault("status", "Backlog")
    task["files"] = []
    task["decisions"] = []
    task["transcript"] = []
    task.setdefault("acceptanceCriteria", [])
    task.setdefault("blockedBy", [])
    task.setdefault("priority", 100)
    task["qaFailure"] = None
    task["userQuestion"] = None
    task["poRoundTrips"] = 0
    return normalize_task(task)


def normalize_board_tasks() -> None:
    """Backfills context fields on tasks loaded from older saved projects."""
    for lane in state.SHARED_BOARD.values():
        for task in lane:
            normalize_task(task)


def set_active_sprint_context(task_id: str, agent_role: str) -> None:
    state.ACTIVE_SPRINT_TASK_ID = task_id
    state.ACTIVE_SPRINT_AGENT = agent_role


def clear_active_sprint_context() -> None:
    state.ACTIVE_SPRINT_TASK_ID = None
    state.ACTIVE_SPRINT_AGENT = None


def record_task_file(task_id: str, path: str, action: str = "written") -> None:
    task = find_task_by_id(task_id)
    if not task:
        return
    normalize_task(task)
    entry = {"path": path, "action": action}
    existing_paths = {f if isinstance(f, str) else f.get("path") for f in task["files"]}
    if path not in existing_paths:
        task["files"].append(entry)


def record_task_decision(
    task_id: str,
    agent: str,
    decision_type: str,
    summary: str,
    detail: str = "",
) -> None:
    task = find_task_by_id(task_id)
    if not task:
        return
    normalize_task(task)
    task["decisions"].append(
        {
            "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "agent": agent,
            "type": decision_type,
            "summary": summary[:500],
            "detail": (detail or "")[:2000],
        }
    )
    if len(task["decisions"]) > MAX_TASK_DECISIONS:
        task["decisions"] = task["decisions"][-MAX_TASK_DECISIONS:]
    publish_activity(
        task_id,
        "decision",
        summary,
        role="decision",
        agent=agent,
    )
    if detail:
        publish_activity(task_id, "decision_detail", detail, role="decision", agent=agent)


def record_task_transcript(
    task_id: str,
    role: str,
    content: str,
    agent: Optional[str] = None,
) -> None:
    task = find_task_by_id(task_id)
    if not task:
        return
    normalize_task(task)
    task["transcript"].append(
        {
            "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "role": role,
            "agent": agent or role,
            "content": content[:4000],
        }
    )
    publish_activity(
        task_id,
        "transcript",
        content,
        role=role,
        agent=agent or role,
    )


def increment_po_round_trips(task_id: str) -> int:
    task = find_task_by_id(task_id)
    if not task:
        return 0
    normalize_task(task)
    task["poRoundTrips"] = int(task.get("poRoundTrips", 0)) + 1
    count = task["poRoundTrips"]
    publish_activity(
        task_id,
        "po_round_trip",
        f"PO round trip #{count}",
        role="system",
        agent="System",
        lane=task.get("status"),
    )
    return count


def set_qa_failure(task_id: str, reason: str, output: str = "") -> None:
    task = find_task_by_id(task_id)
    if not task:
        return
    normalize_task(task)
    task["qaFailure"] = {
        "reason": reason[:500],
        "output": output[:2000],
        "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


def clear_qa_failure(task_id: str) -> None:
    task = find_task_by_id(task_id)
    if not task:
        return
    normalize_task(task)
    task["qaFailure"] = None


def apply_po_clarification(
    task_id: str,
    description: Optional[str] = None,
    acceptance_criteria: Optional[List[str]] = None,
) -> None:
    task = find_task_by_id(task_id)
    if not task:
        return
    normalize_task(task)
    if description:
        task["description"] = description
    if acceptance_criteria:
        task["acceptanceCriteria"] = acceptance_criteria


def sort_backlog() -> None:
    backlog = state.SHARED_BOARD.get("Backlog", [])
    backlog.sort(key=lambda t: (t.get("priority", 100), t.get("id", "")))


def next_claimable_backlog_task() -> Optional[Dict[str, Any]]:
    sort_backlog()
    for task in state.SHARED_BOARD.get("Backlog", []):
        normalize_task(task)
        if task_dependencies_met(task):
            return task
    return None


def build_dod_block() -> str:
    settings = get_workflow_settings()
    dod = settings.get("definitionOfDone") or []
    if not dod:
        return ""
    lines = "\n".join(f"- {item}" for item in dod)
    return f"\n=== DEFINITION OF DONE (project) ===\n{lines}\n"


def build_task_prompt(task: Dict[str, Any], brief: str) -> str:
    """Builds a structured prompt for sprint agents."""
    normalize_task(task)
    file_list = ", ".join(state.VIRTUAL_FILESYSTEM.keys()) or "(empty workspace)"
    task_files = task["files"]
    task_file_lines = []
    for f in task_files:
        if isinstance(f, str):
            task_file_lines.append(f)
        else:
            task_file_lines.append(f"{f.get('path', '?')} ({f.get('action', 'touched')})")
    task_files_str = ", ".join(task_file_lines) if task_file_lines else "(none yet)"

    ac_lines = task.get("acceptanceCriteria") or []
    ac_str = "\n".join(f"- {c}" for c in ac_lines) if ac_lines else "(none defined)"

    blocked = task.get("blockedBy") or []
    blocked_str = ", ".join(blocked) if blocked else "(none)"

    prompt = (
        f"Project brief:\n{brief}\n"
        f"{build_dod_block()}\n"
        f"Task ID: {task['id']}\n"
        f"Title: {task['title']}\n"
        f"Description: {task['description']}\n"
        f"Acceptance criteria:\n{ac_str}\n"
        f"Blocked by (must be Done first): {blocked_str}\n"
        f"Current status: {task.get('status', 'unknown')}\n"
        f"Workspace files: {file_list}\n"
        f"Files associated with this card: {task_files_str}\n"
    )

    qa_fail = task.get("qaFailure")
    if qa_fail:
        prompt += (
            "\n=== LAST QA FAILURE ===\n"
            f"Reason: {qa_fail.get('reason', '')}\n"
            f"Output: {qa_fail.get('output', '')[:500]}\n"
            f"When: {qa_fail.get('timestamp', '')}\n"
        )

    if task.get("userQuestion"):
        prompt += f"\n=== USER QUESTION PENDING ===\n{task['userQuestion']}\n"

    if task["decisions"]:
        prompt += "\n=== PRIOR AGENT DECISIONS ON THIS CARD ===\n"
        for d in task["decisions"][-8:]:
            prompt += (
                f"[{d.get('timestamp', '?')}] {d.get('agent', 'Agent')} "
                f"({d.get('type', 'note')}): {d.get('summary', '')}\n"
            )
            if d.get("detail"):
                prompt += f"  Detail: {d['detail'][:300]}\n"

    if task["transcript"]:
        prompt += "\n=== TASK TRANSCRIPT ===\n"
        for entry in task["transcript"][-6:]:
            prompt += f"[{entry.get('timestamp', '?')}] {entry.get('agent', entry.get('role', '?'))}: {entry.get('content', '')[:200]}\n"

    return prompt
