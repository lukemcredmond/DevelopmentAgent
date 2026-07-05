from typing import Any, Dict, List, Optional

from backend import state
from backend.agents.task_context import find_task_by_id, record_task_decision
from backend.services.events import publish_event
from backend.services.project_service import save_current_project_state


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
        if target_lane not in state.SHARED_BOARD:
            state.SHARED_BOARD[target_lane] = []
        state.SHARED_BOARD[target_lane].append(active_task)
        record_task_decision(
            active_task["id"],
            state.ACTIVE_SPRINT_AGENT or "System",
            "move",
            f"Moved from '{source_lane}' to '{target_lane}'",
        )
        save_current_project_state()
        publish_board_update(task_id, target_lane, source="move")
        return f"Successfully moved task {task_id} to '{target_lane}'."
