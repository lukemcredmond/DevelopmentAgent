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
    ClaimReadyPayload,
    DeleteTaskPayload,
    DoneAuditApplyPayload,
    EscapeSubtaskPayload,
    InjectToolEvidencePayload,
    ManualTaskPayload,
    MoveTaskPayload,
    ReorderTasksPayload,
    RefinementAuditApplyPayload,
    ResolveUserPayload,
    SplitTaskPayload,
    DiagnoseTaskPayload,
    UpdateTaskPayload,
)
from backend.services.board_lanes import normalize_board_lanes
from backend.services.board_service import (
    claim_ready_backlog_tasks,
    clear_all_board_tasks,
    move_board_stage,
    publish_board_update,
)
from backend.services.done_audit import apply_done_audit_actions, audit_done_tasks
from backend.services.refinement_audit import apply_refinement_audit_actions, audit_refinement_lane
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
        run_po_add_feature(
            payload.title,
            payload.description,
            payload.ollama_url,
            preferred_feature_id=payload.preferredFeatureId,
        )
        add_system_log("System", "success", f"Feature '{payload.title}' sent to PO.")
    return build_state_response()


@router.post("/api/tasks/move")
def move_task(payload: MoveTaskPayload):
    with state.STATE_LOCK:
        task = find_task_by_id(payload.task_id)
        if task:
            normalize_task(task)
            if task.get("workType") == "feature" and payload.target_lane != "Features":
                raise HTTPException(
                    status_code=400,
                    detail=f"Feature '{payload.task_id}' is stationary and cannot be moved.",
                )
        if payload.skip_refinement and payload.target_lane == "In Progress":
            task = find_task_by_id(payload.task_id)
            if task:
                normalize_task(task)
                task["refinementComplete"] = True
        result = move_board_stage(payload.task_id, payload.target_lane)
        if result.startswith("Error"):
            raise HTTPException(status_code=404, detail=result)
        add_system_log("System", "info", result)
    return build_state_response()


@router.post("/api/board/claim-ready")
def claim_ready_cards(payload: ClaimReadyPayload):
    from backend.agents.agent_run import get_active_run
    from backend.agents.task_context import count_claimable_backlog_tasks

    with state.STATE_LOCK:
        if get_active_run() is not None:
            raise HTTPException(
                status_code=409,
                detail="Cannot claim cards while an agent sprint step is running.",
            )
        if count_claimable_backlog_tasks() == 0:
            return {**build_state_response(), "claimedTaskIds": [], "readyCount": 0}
        claimed = claim_ready_backlog_tasks(limit=payload.limit)
        remaining = count_claimable_backlog_tasks()
    return {
        **build_state_response(),
        "claimedTaskIds": claimed,
        "readyCount": remaining,
    }


def _apply_task_update(task: dict, payload: UpdateTaskPayload) -> None:
    normalize_task(task)
    before = {
        "title": task.get("title"),
        "description": task.get("description"),
        "acceptanceCriteria": list(task.get("acceptanceCriteria") or []),
        "userStory": task.get("userStory"),
        "scope": task.get("scope"),
        "outOfScope": task.get("outOfScope"),
        "testPlan": task.get("testPlan"),
    }
    changed_keys: list[str] = []
    if payload.title is not None:
        task["title"] = payload.title
        changed_keys.append("title")
    if payload.description is not None:
        task["description"] = payload.description
        changed_keys.append("description")
    if payload.acceptanceCriteria is not None:
        task["acceptanceCriteria"] = payload.acceptanceCriteria
        changed_keys.append("acceptanceCriteria")
        acs = [str(c).strip() for c in payload.acceptanceCriteria if str(c).strip()]
        checks = list(task.get("acChecklist") or [])
        while len(checks) < len(acs):
            checks.append(False)
        task["acChecklist"] = [bool(x) for x in checks[: len(acs)]]
    if payload.acChecklist is not None:
        acs = [str(c).strip() for c in (task.get("acceptanceCriteria") or []) if str(c).strip()]
        checks = list(payload.acChecklist)
        while len(checks) < len(acs):
            checks.append(False)
        task["acChecklist"] = [bool(x) for x in checks[: len(acs)]]
    if payload.blockedBy is not None:
        task["blockedBy"] = payload.blockedBy
    if payload.priority is not None:
        task["priority"] = payload.priority
    if payload.userStory is not None:
        task["userStory"] = payload.userStory
        changed_keys.append("userStory")
    if payload.scope is not None:
        task["scope"] = payload.scope
        changed_keys.append("scope")
    if payload.outOfScope is not None:
        task["outOfScope"] = payload.outOfScope
        changed_keys.append("outOfScope")
    if payload.testPlan is not None:
        task["testPlan"] = payload.testPlan
        changed_keys.append("testPlan")
    if payload.focusMode is not None:
        mode = str(payload.focusMode).strip().lower()
        if mode in ("ac", "subtask", "whole"):
            task["focusMode"] = mode
    if payload.focusAcIndex is not None:
        task["focusAcIndex"] = int(payload.focusAcIndex)
    if payload.focusSubtaskId is not None:
        task["focusSubtaskId"] = str(payload.focusSubtaskId).strip() or None
    if payload.focusPackPaths is not None:
        task["focusPackPaths"] = [str(p).strip() for p in payload.focusPackPaths if str(p).strip()]
    if payload.recommendedSkillFiles is not None:
        task["recommendedSkillFiles"] = [
            str(s).strip() for s in payload.recommendedSkillFiles if str(s).strip()
        ]
    normalize_task(task)
    from backend.services.card_delivery import (
        build_expected_summary,
        sync_card_delivery_fields,
        update_ac_verification_from_checklist,
    )

    rebuild_expected = (
        payload.title is not None
        or payload.description is not None
        or payload.acceptanceCriteria is not None
        or payload.testPlan is not None
    )
    if payload.actualSummary is not None:
        task["actualSummary"] = payload.actualSummary
    if rebuild_expected:
        task["expectedSummary"] = build_expected_summary(task)
    if payload.acChecklist is not None:
        update_ac_verification_from_checklist(task, note="User checklist update")
    else:
        sync_card_delivery_fields(task, rebuild_expected=rebuild_expected)
    normalize_task(task)
    if changed_keys:
        try:
            from backend.services.task_field_history import record_task_fields_from_update

            record_task_fields_from_update(task, before=before, source="user", changed_keys=changed_keys)
        except Exception:
            pass


@router.post("/api/tasks/update")
def update_task(payload: UpdateTaskPayload):
    with state.STATE_LOCK:
        if not payload.task_id:
            raise HTTPException(status_code=400, detail="task_id required")
        task = find_task_by_id(payload.task_id)
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")
        _apply_task_update(task, payload)
        try:
            from backend.services.task_spec_markdown import sync_task_spec_docs

            sync_task_spec_docs(payload.task_id)
        except Exception:
            pass
        save_current_project_state()
        add_system_log("System", "info", f"Updated task {payload.task_id}")
    return build_state_response()


@router.patch("/api/tasks/{task_id}")
def patch_task(task_id: str, payload: UpdateTaskPayload):
    return update_task(UpdateTaskPayload(task_id=task_id, **payload.model_dump(exclude={"task_id"}, exclude_none=True)))


@router.get("/api/tasks/{task_id}/field-history")
def get_task_field_history(task_id: str, field: str = "description", limit: int = 40):
    from backend.services.task_field_history import (
        TRACKED_FIELDS,
        ensure_baseline_snapshot,
        list_field_history,
    )

    field_key = str(field or "").strip()
    if field_key not in TRACKED_FIELDS:
        raise HTTPException(
            status_code=400,
            detail=f"field must be one of: {', '.join(TRACKED_FIELDS)}",
        )
    with state.STATE_LOCK:
        task = find_task_by_id(task_id)
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")
        try:
            ensure_baseline_snapshot(task, field_key)
        except Exception:
            pass
        entries = list_field_history(task_id, field_key, limit=min(max(limit, 1), 80))
    return {"ok": True, "taskId": task_id, "field": field_key, "entries": entries}


@router.get("/api/tasks/{task_id}/field-history/{entry_id}")
def get_task_field_history_entry(task_id: str, entry_id: str):
    from backend.services.task_field_history import get_field_history_entry

    with state.STATE_LOCK:
        task = find_task_by_id(task_id)
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")
        entry = get_field_history_entry(entry_id)
        if not entry or str(entry.get("taskId") or "") != str(task_id):
            raise HTTPException(status_code=404, detail="History entry not found")
    return {"ok": True, "entry": entry}


@router.post("/api/tasks/{task_id}/focus-advance")
def focus_advance_task(task_id: str):
    with state.STATE_LOCK:
        task = find_task_by_id(task_id)
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")
        from backend.services.focus_slice import advance_focus, ensure_focus_initialized

        ensure_focus_initialized(task)
        if not advance_focus(task):
            raise HTTPException(status_code=400, detail="No further focus slice")
        save_current_project_state()
        add_system_log("System", "info", f"Advanced focus slice on {task_id}")
    return build_state_response()


@router.post("/api/tasks/{task_id}/focus-reset")
def focus_reset_task(task_id: str):
    with state.STATE_LOCK:
        task = find_task_by_id(task_id)
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")
        from backend.services.focus_slice import reset_focus

        reset_focus(task)
        save_current_project_state()
        add_system_log("System", "info", f"Reset focus on {task_id}")
    return build_state_response()


@router.post("/api/tasks/{task_id}/clear-tool-fingerprints")
def clear_tool_fingerprints_task(task_id: str):
    with state.STATE_LOCK:
        task = find_task_by_id(task_id)
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")
        from backend.agents.tool_fingerprints import clear_fingerprint_escalation_state

        clear_fingerprint_escalation_state(task)
        save_current_project_state()
        add_system_log("System", "info", f"Cleared blocked tool fingerprints on {task_id}")
    return build_state_response()


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
            append_user_resolution(task, str(note), "Bulk routed to PO", "Needs PO")
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


@router.get("/api/tasks/{task_id}/flow")
def get_task_flow(task_id: str, limit: int = 80, offset: int = 0, order: str = "desc", includeFull: int = 1):
    """Ordered LLM↔tool flow for a card (from SQLite logs + diagnostics; not board memory)."""
    with state.STATE_LOCK:
        task = find_task_by_id(task_id)
        if not task:
            raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")
    from backend.services.task_flow import build_task_flow

    lim = max(1, min(int(limit or 80), 200))
    off = max(0, min(int(offset or 0), 5000))
    return build_task_flow(
        task_id,
        limit=lim,
        offset=off,
        order=order or "desc",
        include_full=bool(includeFull),
    )


@router.get("/api/tasks/{task_id}/flow/summary")
def get_task_flow_summary(task_id: str, limit: int = 80):
    """Per work-item LLM/tool counts for the Agent progress list (no prompt bodies)."""
    with state.STATE_LOCK:
        task = find_task_by_id(task_id)
        if not task:
            raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")
    from backend.services.task_flow import build_task_flow_summary

    lim = max(1, min(int(limit or 80), 200))
    return build_task_flow_summary(task_id, limit=lim)


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


@router.get("/api/board/done-audit")
def get_done_audit():
    with state.STATE_LOCK:
        report = audit_done_tasks(state.SHARED_BOARD)
    return report


@router.post("/api/board/done-audit/apply")
def apply_done_audit(payload: DoneAuditApplyPayload):
    with state.STATE_LOCK:
        task_ids = list(payload.task_ids or [])
        if not task_ids:
            report = audit_done_tasks(state.SHARED_BOARD)
            task_ids = [str(i["taskId"]) for i in (report.get("items") or []) if i.get("taskId")]
    result = apply_done_audit_actions(
        task_ids,
        payload.move_to,
        only_incomplete=payload.only_incomplete,
    )
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error") or "Apply failed")
    with state.STATE_LOCK:
        add_system_log(
            "System",
            "info",
            f"Done audit: moved {len(result.get('moved') or [])} card(s) to {payload.move_to}",
        )
    return {**build_state_response(), "auditResult": result}


@router.get("/api/board/refinement-audit")
def get_refinement_audit():
    with state.STATE_LOCK:
        report = audit_refinement_lane(state.SHARED_BOARD)
    return report


@router.post("/api/board/refinement-audit/apply")
def apply_refinement_audit(payload: RefinementAuditApplyPayload):
    from backend.agents.agent_run import get_active_run

    with state.STATE_LOCK:
        if get_active_run() is not None:
            raise HTTPException(
                status_code=409,
                detail="Cannot apply refinement cleanup while an agent sprint step is running.",
            )
    result = apply_refinement_audit_actions(
        delete_task_ids=payload.delete_task_ids,
        move_to_done_task_ids=payload.move_to_done_task_ids,
        move_to_backlog_task_ids=payload.move_to_backlog_task_ids,
        duplicate_of_by_task_id=payload.duplicate_of_by_task_id,
    )
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error") or "Apply failed")
    with state.STATE_LOCK:
        add_system_log(
            "System",
            "info",
            "Refinement audit: "
            f"deleted {len(result.get('deleted') or [])}, "
            f"Done {len(result.get('movedDone') or [])}, "
            f"Backlog {len(result.get('movedBacklog') or [])}",
        )
    return {**build_state_response(), "refinementAuditResult": result}


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
