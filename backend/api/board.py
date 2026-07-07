from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException

from backend import state
from backend.agents.task_context import (
    clear_task_transcript,
    find_task_by_id,
    init_refinement_fields,
    normalize_task,
    record_task_decision,
    record_task_transcript,
    sort_backlog,
)
from backend.api.helpers import build_state_response
from backend.api.schemas import (
    DeleteTaskPayload,
    EscapeSubtaskPayload,
    InjectToolEvidencePayload,
    ManualTaskPayload,
    MoveTaskPayload,
    ReorderTasksPayload,
    ResolveUserPayload,
    SplitTaskPayload,
    DiagnoseTaskPayload,
    UpdateTaskPayload,
)
from backend.services.board_lanes import normalize_board_lanes
from backend.services.board_service import clear_all_board_tasks, move_board_stage, publish_board_update
from backend.services.logs import add_system_log
from backend.services.needs_user_guard import append_user_resolution, set_needs_user_cooldown
from backend.services.project_service import save_current_project_state
from backend.services.sprint_service import inject_tool_evidence_for_task, run_po_add_feature, run_po_split_task

router = APIRouter()


@router.post("/api/board/clear-tasks")
def clear_board_tasks():
    from backend.agents.agent_run import get_active_run

    with state.STATE_LOCK:
        if get_active_run() is not None:
            raise HTTPException(
                status_code=409,
                detail="Cannot clear tasks while an agent sprint step is running.",
            )
        clear_all_board_tasks()
    return build_state_response()


@router.post("/api/tasks/manual")
def add_manual_task(payload: ManualTaskPayload):
    with state.STATE_LOCK:
        run_po_add_feature(payload.title, payload.description, payload.ollama_url)
        add_system_log("System", "success", f"Feature '{payload.title}' sent to PO.")
    return build_state_response()


@router.post("/api/tasks/move")
def move_task(payload: MoveTaskPayload):
    with state.STATE_LOCK:
        result = move_board_stage(payload.task_id, payload.target_lane)
        if result.startswith("Error"):
            raise HTTPException(status_code=404, detail=result)
        add_system_log("System", "info", result)
    return build_state_response()


def _apply_task_update(task: dict, payload: UpdateTaskPayload) -> None:
    normalize_task(task)
    if payload.title is not None:
        task["title"] = payload.title
    if payload.description is not None:
        task["description"] = payload.description
    if payload.acceptanceCriteria is not None:
        task["acceptanceCriteria"] = payload.acceptanceCriteria
    if payload.blockedBy is not None:
        task["blockedBy"] = payload.blockedBy
    if payload.priority is not None:
        task["priority"] = payload.priority


@router.post("/api/tasks/update")
def update_task(payload: UpdateTaskPayload):
    with state.STATE_LOCK:
        if not payload.task_id:
            raise HTTPException(status_code=400, detail="task_id required")
        task = find_task_by_id(payload.task_id)
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")
        _apply_task_update(task, payload)
        save_current_project_state()
        add_system_log("System", "info", f"Updated task {payload.task_id}")
    return build_state_response()


@router.patch("/api/tasks/{task_id}")
def patch_task(task_id: str, payload: UpdateTaskPayload):
    return update_task(UpdateTaskPayload(task_id=task_id, **payload.model_dump(exclude={"task_id"}, exclude_none=True)))


@router.post("/api/tasks/{task_id}/approve")
def approve_task(task_id: str):
    with state.STATE_LOCK:
        task = find_task_by_id(task_id)
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")
        if not any(t.get("id") == task_id for t in state.SHARED_BOARD.get("Pending Approval", [])):
            raise HTTPException(status_code=400, detail="Task is not pending approval")
        move_board_stage(task_id, "Backlog")
        sort_backlog()
        record_task_decision(task_id, "User", "approve", "User approved feature for development")
        add_system_log("System", "success", f"Approved {task_id} → Backlog")
    return build_state_response()


@router.post("/api/tasks/{task_id}/resolve-user")
def resolve_user_question(task_id: str, payload: ResolveUserPayload):
    target = (payload.target or "dev").strip().lower()
    if target not in ("dev", "refinement", "po"):
        raise HTTPException(status_code=400, detail="target must be dev, refinement, or po")

    lane_map = {
        "dev": "In Progress",
        "refinement": "Refinement",
        "po": "Needs PO",
    }
    target_lane = lane_map[target]

    with state.STATE_LOCK:
        task = find_task_by_id(task_id)
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")
        if not any(t.get("id") == task_id for t in state.SHARED_BOARD.get("Needs User", [])):
            raise HTTPException(status_code=400, detail="Task is not in Needs User")
        normalize_task(task)
        answer = payload.answer.strip()
        prior_question = (
            task.get("userQuestion")
            or task.get("needsUserReason")
            or task.get("needsUserAction")
            or ""
        )
        append_user_resolution(task, str(prior_question), answer, target_lane)
        set_needs_user_cooldown(task)
        task["needsUserDuplicate"] = False
        record_task_transcript(
            task_id,
            "user",
            f"User response (→ {target_lane}):\n{answer}",
            agent="User",
        )
        task["userQuestion"] = None
        task["needsUserReason"] = None
        task["needsUserAction"] = None
        record_task_decision(
            task_id,
            "User",
            "resolve",
            f"User routed to {target_lane}",
            answer[:500],
        )
        if target == "refinement":
            init_refinement_fields(task)
            task["refinementStatus"] = "pending"
            task["refinementNotes"] = answer
        move_board_stage(task_id, target_lane)
        add_system_log("System", "success", f"User resolved {task_id} → {target_lane}")
    return build_state_response()


@router.post("/api/board/escalate-needs-user-to-po")
def escalate_needs_user_to_po():
    """Move all Needs User cards to Needs PO (bulk clarification routing)."""
    moved: List[str] = []
    with state.STATE_LOCK:
        tasks = list(state.SHARED_BOARD.get("Needs User", []))
        for task in tasks:
            normalize_task(task)
            task_id = str(task.get("id", ""))
            if not task_id:
                continue
            note = (
                task.get("needsUserReason")
                or task.get("userQuestion")
                or "User bulk-routed clarification to PO"
            )
            task["userQuestion"] = None
            task["needsUserReason"] = None
            task["needsUserAction"] = None
            task["needsUserDuplicate"] = False
            record_task_decision(
                task_id,
                "User",
                "bulk_escalate",
                "Bulk routed Needs User → Needs PO",
                str(note)[:500],
            )
            move_board_stage(task_id, "Needs PO")
            moved.append(task_id)
        if moved:
            add_system_log(
                "System",
                "success",
                f"Bulk routed {len(moved)} card(s) from Needs User → Needs PO",
            )
    return {**build_state_response(), "movedTaskIds": moved}


@router.post("/api/tasks/{task_id}/inject-tool-evidence")
def inject_tool_evidence(task_id: str, payload: InjectToolEvidencePayload):
    with state.STATE_LOCK:
        if not find_task_by_id(task_id):
            raise HTTPException(status_code=404, detail="Task not found")
        if not payload.toolOutput.strip():
            raise HTTPException(status_code=400, detail="toolOutput is required")
        try:
            result = inject_tool_evidence_for_task(
                task_id,
                payload.toolName,
                payload.toolArgs,
                payload.toolOutput,
                note=payload.note,
            )
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {**build_state_response(), "injectResult": result}


@router.post("/api/tasks/{task_id}/diagnose")
def diagnose_task_route(task_id: str, payload: DiagnoseTaskPayload):
    from backend.services.task_diagnosis import diagnose_task

    with state.STATE_LOCK:
        if not find_task_by_id(task_id):
            raise HTTPException(status_code=404, detail="Task not found")
        result = diagnose_task(task_id, payload.ollamaUrl)
        if not result.get("ok"):
            raise HTTPException(status_code=400, detail=result.get("error", "Diagnosis failed"))
    return {"state": build_state_response(), "diagnosis": result.get("diagnosis")}


@router.post("/api/tasks/{task_id}/split")
def split_task(task_id: str, payload: SplitTaskPayload):
    from backend.agents.agent_run import get_active_run

    with state.STATE_LOCK:
        if get_active_run() is not None:
            raise HTTPException(
                status_code=409,
                detail="Cannot split a task while an agent sprint step is running.",
            )
        task = find_task_by_id(task_id)
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")
        if any(t.get("id") == task_id for t in state.SHARED_BOARD.get("Done", [])):
            raise HTTPException(status_code=400, detail="Cannot split a Done task")
        try:
            split_result = run_po_split_task(task_id, payload.ollama_url, payload.guidance)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {**build_state_response(), "splitResult": split_result}


@router.post("/api/tasks/reorder")
def reorder_tasks(payload: ReorderTasksPayload):
    with state.STATE_LOCK:
        lane = payload.lane
        if lane not in state.SHARED_BOARD:
            raise HTTPException(status_code=400, detail=f"Unknown lane: {lane}")
        tasks_by_id = {t["id"]: t for t in state.SHARED_BOARD[lane]}
        reordered = []
        for i, tid in enumerate(payload.taskIds):
            if tid in tasks_by_id:
                tasks_by_id[tid]["priority"] = i + 1
                if lane == "Refinement":
                    tasks_by_id[tid]["executionOrder"] = i + 1
                reordered.append(tasks_by_id[tid])
        for t in state.SHARED_BOARD[lane]:
            if t["id"] not in payload.taskIds:
                reordered.append(t)
        state.SHARED_BOARD[lane] = reordered
        save_current_project_state()
        publish_board_update(source="reorder")
    return build_state_response()


@router.post("/api/tasks/{task_id}/escape-subtasks")
def escape_subtasks_route(task_id: str, payload: EscapeSubtaskPayload):
    from backend.services.subtask_service import escape_subtask_loop

    result = escape_subtask_loop(task_id, mode=payload.mode or "needs_po")
    if result.startswith("Error"):
        raise HTTPException(status_code=400, detail=result)
    return {**build_state_response(), "message": result}


@router.delete("/api/tasks/{task_id}/transcript")
def clear_task_transcript_route(task_id: str):
    with state.STATE_LOCK:
        if not clear_task_transcript(task_id):
            raise HTTPException(
                status_code=404,
                detail=f"Task not found: {task_id}",
            )
        save_current_project_state()
        add_system_log("System", "info", f"Cleared transcript for {task_id}")
    return build_state_response()


@router.delete("/api/tasks/{task_id}")
def delete_task_by_id(task_id: str):
    return delete_task(DeleteTaskPayload(task_id=task_id))


@router.post("/api/tasks/delete")
def delete_task(payload: DeleteTaskPayload):
    with state.STATE_LOCK:
        removed = False
        for lane, tasks in state.SHARED_BOARD.items():
            for task in list(tasks):
                if str(task.get("id", "")) == str(payload.task_id):
                    tasks.remove(task)
                    removed = True
                    break
            if removed:
                break
        if not removed:
            raise HTTPException(status_code=404, detail="Task not found")
        save_current_project_state()
        add_system_log("System", "info", f"Deleted task {payload.task_id}")
        publish_board_update(payload.task_id, source="delete")
    return build_state_response()
