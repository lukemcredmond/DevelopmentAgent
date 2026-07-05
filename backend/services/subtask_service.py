"""Hierarchical backlog todos: parent tasks spawn ordered subtasks that must complete first."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from backend import state
from backend.agents.task_context import (
    coerce_task_text,
    find_task_by_id,
    get_task_lane,
    init_new_task,
    is_task_done,
    normalize_task,
    record_task_decision,
    sort_backlog,
)
from backend.services.board_service import publish_board_update
from backend.services.logs import add_system_log
from backend.services.project_service import save_current_project_state
from backend.services.workflow_settings import get_workflow_settings


def subtask_depth(task_id: str) -> int:
    depth = 0
    current = str(task_id)
    seen: set[str] = set()
    while current:
        if current in seen:
            return depth
        seen.add(current)
        task = find_task_by_id(current)
        if not task:
            break
        parent = task.get("parentTaskId")
        if not parent:
            break
        depth += 1
        current = str(parent)
    return depth


def pending_subtask_ids(task: Dict[str, Any]) -> List[str]:
    normalize_task(task)
    pending: List[str] = []
    for sid in task.get("subtaskIds") or []:
        if not is_task_done(str(sid)):
            pending.append(str(sid))
    return pending


def all_subtasks_complete(task: Dict[str, Any]) -> bool:
    return len(pending_subtask_ids(task)) == 0


def subtask_gate_blocks_advance(task: Dict[str, Any]) -> Tuple[bool, str]:
    pending = pending_subtask_ids(task)
    if not pending:
        return False, ""
    return True, f"{len(pending)} subtask(s) must reach Done first: {', '.join(pending[:5])}"


def _max_subtask_depth() -> int:
    return int(get_workflow_settings().get("maxSubtaskDepth") or 4)


def _max_subtask_spawns() -> int:
    return int(get_workflow_settings().get("maxSubtaskSpawns") or 8)


def _can_spawn_subtasks(parent_id: str) -> Tuple[bool, str]:
    parent = find_task_by_id(parent_id)
    if not parent:
        return False, f"Parent task '{parent_id}' not found."
    depth = subtask_depth(parent_id)
    if depth >= _max_subtask_depth():
        return (
            False,
            f"Max subtask depth ({_max_subtask_depth()}) reached — use escape_subtask_loop or Needs PO.",
        )
    spawns = int(parent.get("subtaskSpawnCount") or 0)
    if spawns >= _max_subtask_spawns():
        return (
            False,
            f"Max subtask spawns ({_max_subtask_spawns()}) for this card — escape the loop or clarify scope.",
        )
    return True, ""


def _ordered_chain_blocked_by(subtask_ids: List[str]) -> Dict[str, List[str]]:
    """Sequential execution: each subtask blocked by the previous sibling."""
    blocked: Dict[str, List[str]] = {}
    prev: Optional[str] = None
    for sid in subtask_ids:
        blocked[sid] = [prev] if prev else []
        prev = sid
    return blocked


def append_subtasks(
    parent_task_id: str,
    raw_tasks: List[Dict[str, Any]],
    *,
    lane: Optional[str] = None,
) -> str:
    """Create child todos under a parent; parent cannot complete until all children are Done."""
    if not raw_tasks:
        return "Error: No subtasks provided."
    if not str(parent_task_id or "").strip():
        return "Error: parent_task_id is required (no active sprint task)."

    ok, reason = _can_spawn_subtasks(parent_task_id)
    if not ok:
        return f"Error: {reason}"

    from backend.services.board_service import _enrich_task_from_po, _new_task_lane
    from backend.agents.task_context import all_task_ids, assign_unique_task_id

    with state.STATE_LOCK:
        parent = find_task_by_id(parent_task_id)
        if not parent:
            return f"Error: Parent task '{parent_task_id}' not found."

        target_lane = lane or _new_task_lane()
        if get_task_lane(parent_task_id) == "Refinement":
            target_lane = "Refinement"
        elif get_task_lane(parent_task_id) == "In Progress":
            target_lane = "Backlog"

        state.SHARED_BOARD.setdefault(target_lane, [])
        existing_ids = all_task_ids()
        prepared: List[Dict[str, Any]] = []
        id_map: Dict[str, str] = {}

        for i, raw in enumerate(raw_tasks):
            task = _enrich_task_from_po(dict(raw))
            po_ref = task.get("id")
            new_id = assign_unique_task_id(task, preserve_po_ref=True, existing_ids=existing_ids)
            if po_ref is not None:
                id_map[str(po_ref)] = new_id
            task["parentTaskId"] = parent_task_id
            if "executionOrder" not in task:
                task["executionOrder"] = int(raw.get("order") or raw.get("executionOrder") or (i + 1))
            prepared.append(task)
            existing_ids.add(new_id)

        prepared.sort(key=lambda t: (t.get("executionOrder", 100), t.get("id", "")))
        new_ids = [str(t["id"]) for t in prepared]
        chain = _ordered_chain_blocked_by(new_ids)

        parent_subtasks = list(parent.get("subtaskIds") or [])
        parent_blocked = list(parent.get("blockedBy") or [])

        for task in prepared:
            tid = str(task["id"])
            task["blockedBy"] = list(chain.get(tid) or [])
            init_new_task(task)
            task["status"] = target_lane
            state.SHARED_BOARD[target_lane].append(task)
            if tid not in parent_subtasks:
                parent_subtasks.append(tid)
            if tid not in parent_blocked:
                parent_blocked.append(tid)

        parent["subtaskIds"] = parent_subtasks
        parent["blockedBy"] = parent_blocked
        parent["subtaskSpawnCount"] = int(parent.get("subtaskSpawnCount") or 0) + len(prepared)

        record_task_decision(
            parent_task_id,
            state.ACTIVE_SPRINT_AGENT or "System",
            "subtasks",
            f"Added {len(prepared)} subtask(s)",
            ", ".join(new_ids),
        )

        if target_lane == "Backlog":
            sort_backlog()
        save_current_project_state()
        publish_board_update(parent_task_id, source="subtasks")

    add_system_log(
        "System",
        "info",
        f"Created {len(prepared)} subtask(s) under {parent_task_id}",
    )
    return f"Added {len(prepared)} subtask(s) under {parent_task_id}: {', '.join(new_ids)}"


def apply_execution_plan(parent_task_id: str, plan: List[Dict[str, Any]]) -> int:
    """Apply an ordered execution plan from refinement (PO sets todo order)."""
    if not plan:
        return 0
    tasks: List[Dict[str, Any]] = []
    for i, item in enumerate(plan):
        if not isinstance(item, dict):
            continue
        title = coerce_task_text(item.get("title") or "").strip()
        if not title:
            continue
        tasks.append(
            {
                "title": title,
                "description": coerce_task_text(item.get("description") or title),
                "acceptanceCriteria": item.get("acceptanceCriteria") or item.get("acceptance_criteria") or [],
                "executionOrder": int(item.get("order") or item.get("executionOrder") or (i + 1)),
                "requiresDev": item.get("requiresDev", True),
                "requiresQa": item.get("requiresQa", True),
            }
        )
    if not tasks:
        return 0
    result = append_subtasks(parent_task_id, tasks)
    if result.startswith("Error"):
        add_system_log("Product Owner", "warning", result)
        return 0
    return len(tasks)


def on_subtask_completed(task_id: str) -> None:
    """When a subtask reaches Done, update parent blockedBy and log progress."""
    task = find_task_by_id(task_id)
    if not task:
        return
    parent_id = task.get("parentTaskId")
    if not parent_id:
        return
    parent = find_task_by_id(str(parent_id))
    if not parent:
        return
    normalize_task(parent)
    blocked = [b for b in (parent.get("blockedBy") or []) if str(b) != str(task_id)]
    parent["blockedBy"] = blocked
    if all_subtasks_complete(parent):
        record_task_decision(
            str(parent_id),
            "System",
            "subtasks_complete",
            "All subtasks Done — parent can proceed",
        )
        add_system_log(
            "System",
            "success",
            f"All subtasks complete for '{parent.get('title', parent_id)}'",
        )


def escape_subtask_loop(task_id: str, *, mode: str = "needs_po") -> str:
    """
    Break out of a subtask decomposition loop.
    mode: needs_po | skip_pending | flatten
    """
    task = find_task_by_id(task_id)
    if not task:
        return f"Error: Task '{task_id}' not found."

    pending = pending_subtask_ids(task)
    if not pending and not task.get("subtaskSpawnCount"):
        return f"Task {task_id} has no active subtask loop to escape."

    with state.STATE_LOCK:
        normalize_task(task)
        for sid in pending:
            sub = find_task_by_id(sid)
            lane = get_task_lane(sid)
            if mode == "skip_pending" and sub and lane:
                sub["status"] = "Done"
                sub["subtaskSkipped"] = True
                needle = str(sid)
                for ln in list(state.SHARED_BOARD.keys()):
                    state.SHARED_BOARD[ln] = [
                        t for t in state.SHARED_BOARD[ln] if str(t.get("id", "")) != needle
                    ]
                state.SHARED_BOARD.setdefault("Done", []).append(sub)
                on_subtask_completed(sid)
            elif mode == "flatten" and sub:
                sub["status"] = "Done"
                sub["subtaskSkipped"] = True

        task["blockedBy"] = [b for b in (task.get("blockedBy") or []) if str(b) not in pending]
        task["subtaskEscapeCount"] = int(task.get("subtaskEscapeCount") or 0) + 1
        task["subtaskSpawnCount"] = 0
        record_task_decision(
            task_id,
            "System",
            "subtask_escape",
            f"Escaped subtask loop ({mode})",
            f"Cleared {len(pending)} pending subtask(s)",
        )
        save_current_project_state()
        publish_board_update(task_id, source="subtask_escape")

    from backend.services.board_service import move_board_stage

    if mode == "needs_po":
        move_board_stage(task_id, "Needs PO")
        return f"Escaped subtask loop on {task_id} — moved to Needs PO for clarification."

    return f"Escaped subtask loop on {task_id} ({mode}) — {len(pending)} pending subtask(s) cleared."
