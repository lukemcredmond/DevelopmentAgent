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
    """Push live board snapshot to SSE subscribers (slimmed + coalesced)."""
    from backend.services.events import publish_board_event_coalesced, slim_board_for_sse

    task = find_task_by_id(task_id) if task_id else None
    publish_board_event_coalesced(
        {
            "board": slim_board_for_sse(state.SHARED_BOARD),
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
    from backend.services.events import _slim_task_for_sse, publish_event

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
            "task": _slim_task_for_sse(dict(task)),
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
        normalize_task(active_task)
        if active_task.get("workType") == "feature" and target_lane != "Features":
            return (
                f"Error: Feature '{task_id}' is stationary — it cannot move to '{target_lane}'."
            )
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
    state.ALLOW_DONE_RETRY = False
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


MAX_AC_FOR_IMPLEMENTATION = 5
OVERSIZE_DESC_CHARS = 800
_OVERSIZE_VERBS = (
    "implement",
    "add",
    "create",
    "build",
    "refactor",
    "migrate",
    "integrate",
    "wire",
    "fix",
    "update",
    "remove",
    "delete",
)


def is_oversized_implementation(task: Dict[str, Any]) -> Optional[str]:
    """Return a rejection reason if this card is too large for one focused pass."""
    if task.get("requiresDev") is False:
        return None
    if str(task.get("workType") or "") == "planning":
        return None
    ac = task.get("acceptanceCriteria") or []
    if len(ac) > MAX_AC_FOR_IMPLEMENTATION:
        return (
            f"Too many acceptance criteria ({len(ac)} > {MAX_AC_FOR_IMPLEMENTATION}). "
            "Split into smaller cards via add_backlog_tasks."
        )
    desc = str(task.get("description") or "")
    if len(desc) > OVERSIZE_DESC_CHARS:
        return (
            f"Description is too long ({len(desc)} chars) for one focused pass. "
            "Split into smaller cards."
        )
    lower = desc.lower()
    verb_hits = sum(1 for v in _OVERSIZE_VERBS if v in lower)
    if verb_hits >= 4:
        return (
            f"Description spans too many concerns ({verb_hits} action verbs). "
            "Split into the smallest achievable cards — one focused change each."
        )
    return None


def _resolve_reuse_requester(source_id: Optional[str], proposed: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if source_id:
        requester = find_task_by_id(source_id)
        if requester:
            return requester
    active_id = state.ACTIVE_SPRINT_TASK_ID
    if active_id:
        requester = find_task_by_id(str(active_id))
        if requester:
            return requester
    feature_id = proposed.get("featureId")
    if feature_id:
        from backend.services.feature_service import find_feature_by_id

        feature = find_feature_by_id(str(feature_id))
        if feature:
            return feature
    return None


def append_backlog_tasks(
    tasks: List[Dict[str, Any]],
    *,
    split_from_task_id: Optional[str] = None,
) -> str:
    """Add tasks to Backlog (or Pending Approval). Optionally split a source task to Done.

    Same-request cards (similarity >= REUSE_THRESHOLD) are not recreated — the existing
    card is linked and its outcomes are attached to the requester instead.
    Oversized implementation cards are rejected with a split instruction.
    """
    if not tasks:
        return "Error: No tasks provided."

    from backend.services.feature_similarity import (
        apply_same_request_reuse,
        find_same_request_match,
        iter_board_tasks,
        link_related_features,
    )

    with state.STATE_LOCK:
        lane = _new_task_lane()
        state.SHARED_BOARD.setdefault(lane, [])
        existing_ids = all_task_ids()
        prepared: List[Dict[str, Any]] = []
        id_map: Dict[str, str] = {}
        oversize_errors: List[str] = []

        for i, raw in enumerate(tasks):
            task = _enrich_task_from_po(raw)
            oversize = is_oversized_implementation(task)
            if oversize:
                title = task.get("title") or f"item {i + 1}"
                oversize_errors.append(f"'{title}': {oversize}")
                continue
            po_ref = task.get("id")
            new_id = assign_unique_task_id(task, preserve_po_ref=True, existing_ids=existing_ids)
            if po_ref is not None:
                id_map[str(po_ref)] = new_id
            elif str(i) not in id_map:
                id_map[str(i)] = new_id
            prepared.append(task)

        if oversize_errors and not prepared:
            return (
                "Error: Card(s) too large for one focused pass — split into smallest parts:\n- "
                + "\n- ".join(oversize_errors)
            )

        batch_ids = {str(t["id"]) for t in prepared}
        prior_candidates = iter_board_tasks(exclude_ids=batch_ids)
        added_tasks: List[Dict[str, Any]] = []
        reused_msgs: List[str] = []
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

            match_result = find_same_request_match(
                task,
                exclude_ids=batch_ids | {str(t["id"]) for t in added_tasks},
                pool=prior_candidates + added_tasks,
            )
            if match_result:
                match, score, reasons = match_result
                requester = _resolve_reuse_requester(source_id, task)
                msg = apply_same_request_reuse(
                    requester,
                    match,
                    score=score,
                    reasons=reasons,
                )
                reused_msgs.append(msg)
                continue

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

        if source_id and added_tasks:
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

        parts: List[str] = []
        if added_tasks:
            ids = ", ".join(str(t["id"]) for t in added_tasks)
            parts.append(f"Added {len(added_tasks)} task(s) to '{lane}': {ids}.")
        if reused_msgs:
            parts.extend(reused_msgs)
        if oversize_errors:
            parts.append(
                "Skipped oversized card(s) — split into smallest parts:\n- "
                + "\n- ".join(oversize_errors)
            )
        if not parts:
            return "No tasks added."
        return " ".join(parts) if len(parts) == 1 else "\n".join(parts)
