from backend import state
from backend.agents.task_context import record_task_decision
from backend.services.project_service import save_current_project_state


def move_board_stage(task_id: str, target_lane: str) -> str:
    active_task = None
    source_lane = None
    for lane, tasks in state.SHARED_BOARD.items():
        for task in tasks:
            if task["id"] == task_id:
                active_task = task
                source_lane = lane
                break
        if active_task:
            break

    if active_task and source_lane is not None:
        state.SHARED_BOARD[source_lane].remove(active_task)
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
        return f"Successfully moved task {task_id} to '{target_lane}'."
    return f"Error: Task '{task_id}' was not found on the board."
