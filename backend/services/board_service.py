from typing import Any, Dict, List, Optional

from backend import state
from backend.agents.task_context import (
    all_task_ids,
    assign_unique_task_id,
    coerce_task_text,
    find_task_by_id,
    get_task_lane,
    init_new_task,
    init_refinement_fields,
    normalize_acceptance_criteria,
    normalize_task,
    record_task_decision,
    reset_refinement_fields,
    sort_backlog,
)
from backend.services.board_lanes import normalize_board_lanes
from backend.services.events import publish_event
from backend.services.logs import add_system_log
from backend.services.project_service import save_current_project_state
from backend.services.workflow_settings import get_workflow_settings


def publish_board_update(
    task_id: Optional[str] = None,
    lane: Optional[str] = None,
    source: str = "move",
) -> None:
    """Push live board snapshot to SSE subscribers."""
    task = find_task_by_id(task_id) if task_id else None
    publish_event(
        "board",
        {
            "board": state.SHARED_BOARD,
            "taskId": task_id,
            "lane": lane,
            "source": source,
            "taskTitle": task.get("title") if task else None,
        },
    )


def publish_board_delta(
    task_id: Optional[str] = None,
    lane: Optional[str] = None,
    source: str = "sprint_step",
) -> None:
    """Push a single-task delta for sprint steps to avoid full board payloads."""
    if not task_id:
        publish_board_update(source=source)
        return
    task = find_task_by_id(task_id)
    if not task:
        publish_board_update(task_id, lane, source=source)
        return
    target_lane = lane or get_task_lane(task_id) or str(task.get("status") or "Backlog")
    publish_event(
        "board",
        {
            "delta": True,
            "taskId": task_id,
            "lane": target_lane,
            "task": dict(task),
            "source": source,
            "taskTitle": task.get("title"),
        },
    )


def move_board_stage(task_id: str, target_lane: str) -> str:
    with state.STATE_LOCK:
        needle = str(task_id)
        matches: List[tuple[str, Dict[str, Any]]] = []
        for lane, tasks in state.SHARED_BOARD.items():
            for task in tasks:
                if str(task.get("id", "")) == needle:
                    matches.append((lane, task))

        if not matches:
            return f"Error: Task '{task_id}' was not found on the board."

        source_lane, active_task = matches[0]
        if len(matches) == 1 and source_lane == target_lane:
            return f"Task {task_id} is already in '{target_lane}'."

        for lane in list(state.SHARED_BOARD.keys()):
            state.SHARED_BOARD[lane] = [
                t for t in state.SHARED_BOARD[lane] if str(t.get("id", "")) != needle
            ]

        active_task["status"] = target_lane
        normalize_task(active_task)
        if target_lane == "Refinement":
            reset_refinement_fields(active_task)
        if target_lane not in state.SHARED_BOARD:
            state.SHARED_BOARD[target_lane] = []
        state.SHARED_BOARD[target_lane].append(active_task)
        if target_lane == "Done":
            from backend.agents.task_context import on_task_completed

            on_task_completed(active_task["id"])
        record_task_decision(
            active_task["id"],
            state.ACTIVE_SPRINT_AGENT or "System",
            "move",
            f"Moved from '{source_lane}' to '{target_lane}'",
        )
        save_current_project_state()
        publish_board_update(task_id, target_lane, source="move")
        return f"Successfully moved task {task_id} to '{target_lane}'."


def claim_ready_backlog_tasks(limit: int = 5) -> List[str]:
    """Move up to N refinement-ready backlog tasks into In Progress, by priority."""
    from backend.agents.task_context import next_claimable_backlog_task

    claimed: List[str] = []
    for _ in range(max(1, min(limit, 50))):
        task = next_claimable_backlog_task()
        if not task:
            break
        task_id = str(task["id"])
        move_board_stage(task_id, "In Progress")
        record_task_decision(task_id, "User", "claim", "Claimed from Backlog (bulk)")
        claimed.append(task_id)
    if claimed:
        add_system_log(
            "System",
            "success",
            f"Claimed {len(claimed)} ready card(s) → In Progress",
        )
    return claimed


def clear_all_board_tasks() -> None:
    """Remove all tasks from every board lane; keep workspace files and brief."""
    normalize_board_lanes(state.SHARED_BOARD)
    for lane in list(state.SHARED_BOARD.keys()):
        state.SHARED_BOARD[lane] = []
    state.ACTIVE_SPRINT_TASK_ID = None
    state.ACTIVE_SPRINT_AGENT = None
    save_current_project_state()
    publish_board_update(source="clear_tasks")
    add_system_log("System", "info", "All board tasks cleared")


def _enrich_task_from_po(raw: Dict[str, Any]) -> Dict[str, Any]:
    task = dict(raw)
    if "acceptanceCriteria" not in task and "acceptance_criteria" in task:
        task["acceptanceCriteria"] = task.pop("acceptance_criteria")
    task["acceptanceCriteria"] = normalize_acceptance_criteria(task.get("acceptanceCriteria"))
    if "description" in task:
        task["description"] = coerce_task_text(task["description"])
    if "title" in task:
        task["title"] = coerce_task_text(task["title"])
    if "blockedBy" not in task and "blocked_by" in task:
        task["blockedBy"] = task.pop("blocked_by")
    if not isinstance(task.get("blockedBy"), list):
        task["blockedBy"] = []
    task.setdefault("priority", 100)
    if "workType" not in task and "work_type" in task:
        task["workType"] = task.pop("work_type")
    if "requiresDev" not in task and "requires_dev" in task:
        task["requiresDev"] = task.pop("requires_dev")
    if "requiresQa" not in task and "requires_qa" in task:
        task["requiresQa"] = task.pop("requires_qa")
    if "createdBy" not in task and "created_by" in task:
        task["createdBy"] = task.pop("created_by")
    if "workType" not in task:
        combined = f"{task.get('title', '')} {task.get('description', '')}".lower()
        planning_kw = ("decompose", "backlog", "split", "clarify", "plan", "epic", "user stor")
        if any(k in combined for k in planning_kw):
            task["workType"] = "planning"
            task.setdefault("requiresDev", False)
            task.setdefault("requiresQa", False)
        else:
            task.setdefault("workType", "implementation")
            task.setdefault("requiresDev", True)
            task.setdefault("requiresQa", True)
    task.setdefault("createdBy", "po")
    return task


def _new_task_lane() -> str:
    ws = get_workflow_settings()
    if ws.get("requireBacklogApproval"):
        return "Pending Approval"
    if ws.get("requireBacklogRefinement"):
        return "Refinement"
    return "Backlog"


def append_backlog_tasks(
    tasks: List[Dict[str, Any]],
    *,
    split_from_task_id: Optional[str] = None,
) -> str:
    """Add tasks to Backlog (or Pending Approval). Optionally split a source task to Done."""
    if not tasks:
        return "Error: No tasks provided."

    from backend.services.feature_similarity import iter_board_tasks, link_related_features

    with state.STATE_LOCK:
        lane = _new_task_lane()
        state.SHARED_BOARD.setdefault(lane, [])
        existing_ids = all_task_ids()
        prepared: List[Dict[str, Any]] = []
        id_map: Dict[str, str] = {}

        for i, raw in enumerate(tasks):
            task = _enrich_task_from_po(raw)
            po_ref = task.get("id")
            new_id = assign_unique_task_id(task, preserve_po_ref=True, existing_ids=existing_ids)
            if po_ref is not None:
                id_map[str(po_ref)] = new_id
            elif str(i) not in id_map:
                id_map[str(i)] = new_id
            prepared.append(task)

        batch_ids = {str(t["id"]) for t in prepared}
        prior_candidates = iter_board_tasks(exclude_ids=batch_ids)
        added_tasks: List[Dict[str, Any]] = []
        source_id = str(split_from_task_id) if split_from_task_id else None

        for task in prepared:
            remapped: List[str] = []
            for ref in task.get("blockedBy") or []:
                ref_str = str(ref)
                if ref_str in id_map:
                    remapped.append(id_map[ref_str])
                elif ref_str in existing_ids:
                    remapped.append(ref_str)
                else:
                    remapped.append(ref_str)
            task["blockedBy"] = remapped
            if source_id:
                related = list(task.get("relatedTaskIds") or [])
                if source_id not in related:
                    related.append(source_id)
                task["relatedTaskIds"] = related
            init_new_task(task)
            link_related_features(
                task,
                exclude_ids=batch_ids,
                candidates=prior_candidates + added_tasks,
            )
            task["status"] = lane
            if lane == "Refinement":
                init_refinement_fields(task)
            state.SHARED_BOARD[lane].append(task)
            added_tasks.append(task)

        if lane == "Backlog":
            sort_backlog()

        if source_id:
            source_task = find_task_by_id(source_id)
            if source_task:
                n = len(added_tasks)
                record_task_decision(
                    source_id,
                    state.ACTIVE_SPRINT_AGENT or "Product Owner",
                    "split",
                    f"Split into {n} subtask(s)",
                    f"Original card replaced by: {', '.join(t['id'] for t in added_tasks)}",
                )
                related = list(source_task.get("relatedTaskIds") or [])
                for t in added_tasks:
                    tid = str(t["id"])
                    if tid not in related:
                        related.append(tid)
                source_task["relatedTaskIds"] = related
                # Inline move to avoid nested lock re-entry issues during board mutation
                needle = source_id
                for ln in list(state.SHARED_BOARD.keys()):
                    state.SHARED_BOARD[ln] = [
                        t for t in state.SHARED_BOARD[ln] if str(t.get("id", "")) != needle
                    ]
                source_task["status"] = "Done"
                state.SHARED_BOARD.setdefault("Done", []).append(source_task)
                record_task_decision(
                    source_id,
                    state.ACTIVE_SPRINT_AGENT or "Product Owner",
                    "move",
                    "Moved from split source to 'Done'",
                )

        save_current_project_state()
        publish_board_update(source="append_tasks")
        ids = ", ".join(str(t["id"]) for t in added_tasks)
        return f"Added {len(added_tasks)} task(s) to '{lane}': {ids}."
