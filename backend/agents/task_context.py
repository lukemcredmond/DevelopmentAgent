import datetime
import json
import uuid
from collections import defaultdict
from typing import Any, Dict, List, Optional, Set, Tuple

from backend.agents.tool_outcomes import FILE_TOOLS, file_path_from_tool
from backend.config import MAX_TASK_DECISIONS, MAX_TASK_TRANSCRIPT
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
            "taskTitle": coerce_task_text(task.get("title", task_id)) if task else task_id,
            "kind": kind,
            "role": role,
            "agent": agent or role,
            "content": content[:4000],
            "lane": lane,
            "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        },
    )


def generate_task_id() -> str:
    return f"TASK-{uuid.uuid4().hex.upper()}"


def all_task_ids() -> Set[str]:
    ids: Set[str] = set()
    for tasks in state.SHARED_BOARD.values():
        for task in tasks:
            tid = str(task.get("id", "")).strip()
            if tid:
                ids.add(tid)
    return ids


def task_id_exists(task_id: str) -> bool:
    return str(task_id) in all_task_ids()


def assign_unique_task_id(
    task: Dict[str, Any],
    *,
    preserve_po_ref: bool = True,
    existing_ids: Optional[Set[str]] = None,
) -> str:
    """Assign a new GUID task id; optionally store the PO-supplied id in poRefId."""
    taken = existing_ids if existing_ids is not None else all_task_ids()
    if preserve_po_ref and task.get("id") is not None:
        task["poRefId"] = str(task["id"])
    new_id = generate_task_id()
    while new_id in taken:
        new_id = generate_task_id()
    task["id"] = new_id
    taken.add(new_id)
    return new_id


def dedupe_board_tasks() -> int:
    """Remove duplicate task ids from the board, keeping one canonical copy per id."""
    by_id: Dict[str, List[Tuple[str, Dict[str, Any]]]] = defaultdict(list)
    for lane, tasks in state.SHARED_BOARD.items():
        for task in tasks:
            tid = str(task.get("id", "")).strip()
            if tid:
                by_id[tid].append((lane, task))

    removed = 0
    for tid, occurrences in by_id.items():
        if len(occurrences) <= 1:
            continue

        keep_lane, keep_task = occurrences[0]
        for lane, task in occurrences:
            if str(task.get("status", "")) == lane:
                keep_lane, keep_task = lane, task
                break

        for lane in list(state.SHARED_BOARD.keys()):
            before = len(state.SHARED_BOARD[lane])
            state.SHARED_BOARD[lane] = [
                t for t in state.SHARED_BOARD[lane] if str(t.get("id", "")) != tid
            ]
            removed += before - len(state.SHARED_BOARD[lane])

        keep_task["status"] = keep_lane
        state.SHARED_BOARD.setdefault(keep_lane, []).append(keep_task)
        removed -= 1

    if removed > 0:
        from backend.services.logs import add_system_log

        add_system_log(
            "System",
            "warning",
            f"Removed {removed} duplicate task card(s) from the board (dedupe by id).",
        )

    return max(0, removed)


def find_task_by_id(task_id: str) -> Optional[Dict[str, Any]]:
    needle = str(task_id)
    for tasks in state.SHARED_BOARD.values():
        for task in tasks:
            if str(task.get("id", "")) == needle:
                return task
    return None


def get_task_lane(task_id: str) -> Optional[str]:
    needle = str(task_id)
    for lane, tasks in state.SHARED_BOARD.items():
        for task in tasks:
            if str(task.get("id", "")) == needle:
                return lane
    return None


def is_task_done(task_id: str) -> bool:
    needle = str(task_id)
    return any(str(t.get("id", "")) == needle for t in state.SHARED_BOARD.get("Done", []))


def task_dependencies_met(task: Dict[str, Any]) -> bool:
    blocked_by = task.get("blockedBy") or []
    return all(is_task_done(dep_id) for dep_id in blocked_by)


def coerce_task_text(value: Any) -> str:
    """Coerce PO/LLM field values to plain strings for storage and UI."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, dict):
        for key in ("description", "text", "criteria", "title", "summary"):
            if key in value and value[key]:
                return coerce_task_text(value[key])
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, list):
        return ", ".join(coerce_task_text(v) for v in value if v is not None)
    return str(value)


def normalize_acceptance_criteria(items: Any) -> List[str]:
    if not isinstance(items, list):
        return []
    result: List[str] = []
    for item in items:
        text = coerce_task_text(item).strip()
        if text:
            result.append(text)
    return result


def normalize_task(task: Dict[str, Any]) -> Dict[str, Any]:
    """Ensures task has context fields for file associations, decisions, and transcript."""
    raw_id = task.get("id")
    if raw_id is not None:
        task["id"] = str(raw_id)
    if not str(task.get("id", "")).strip():
        task["id"] = generate_task_id()
    if "title" in task:
        task["title"] = coerce_task_text(task["title"])
    if "description" in task:
        task["description"] = coerce_task_text(task["description"])
    if task.get("userQuestion") is not None:
        task["userQuestion"] = coerce_task_text(task["userQuestion"])
    if "files" not in task or not isinstance(task["files"], list):
        task["files"] = []
    else:
        normalized_files: List[Dict[str, Any]] = []
        for f in task["files"]:
            if isinstance(f, str):
                normalized_files.append({"path": f, "action": "touched"})
            elif isinstance(f, dict) and f.get("path"):
                normalized_files.append(
                    {
                        "path": str(f["path"]),
                        "action": str(f.get("action") or "touched"),
                        **({"lastTouchedAt": f["lastTouchedAt"]} if f.get("lastTouchedAt") else {}),
                    }
                )
        task["files"] = normalized_files
    if "relatedTaskIds" not in task or not isinstance(task.get("relatedTaskIds"), list):
        task["relatedTaskIds"] = []
    else:
        task["relatedTaskIds"] = [str(r) for r in task["relatedTaskIds"] if r]
    if task.get("gitCommit") is not None and isinstance(task["gitCommit"], dict):
        gc = task["gitCommit"]
        task["gitCommit"] = {
            "hash": coerce_task_text(gc.get("hash")),
            "message": coerce_task_text(gc.get("message", "")),
            "timestamp": coerce_task_text(gc.get("timestamp", "")),
            **({"remoteUrl": coerce_task_text(gc["remoteUrl"])} if gc.get("remoteUrl") else {}),
        }
    elif task.get("gitCommit") is not None:
        task["gitCommit"] = None
    if "decisions" not in task or not isinstance(task["decisions"], list):
        task["decisions"] = []
    if "transcript" not in task or not isinstance(task["transcript"], list):
        task["transcript"] = []
    if "acceptanceCriteria" not in task or not isinstance(task["acceptanceCriteria"], list):
        task["acceptanceCriteria"] = []
    else:
        task["acceptanceCriteria"] = normalize_acceptance_criteria(task["acceptanceCriteria"])
    if "blockedBy" not in task or not isinstance(task["blockedBy"], list):
        task["blockedBy"] = []
    if "priority" not in task or not isinstance(task["priority"], (int, float)):
        task["priority"] = 100
    if task.get("qaFailure") is not None and isinstance(task["qaFailure"], dict):
        qf = task["qaFailure"]
        task["qaFailure"] = {
            "reason": coerce_task_text(qf.get("reason")),
            "output": coerce_task_text(qf.get("output", "")),
            "timestamp": coerce_task_text(qf.get("timestamp", "")),
        }
    elif task.get("qaFailure") is not None:
        task["qaFailure"] = None
    if task.get("qaEvidence") is not None and isinstance(task["qaEvidence"], dict):
        qe = task["qaEvidence"]
        task["qaEvidence"] = {
            "playbookRun": bool(qe.get("playbookRun")),
            "commands": [str(c) for c in (qe.get("commands") or [])],
            "passed": bool(qe.get("passed")),
            **({"userOverride": bool(qe.get("userOverride"))} if qe.get("userOverride") else {}),
        }
    elif "qaEvidence" not in task:
        task["qaEvidence"] = None
    if "userQuestion" not in task:
        task["userQuestion"] = None
    if task.get("needsUserReason") is not None:
        task["needsUserReason"] = coerce_task_text(task["needsUserReason"])
    if task.get("needsUserAction") is not None:
        task["needsUserAction"] = coerce_task_text(task["needsUserAction"])
    if "needsUserReason" not in task:
        task["needsUserReason"] = None
    if "needsUserAction" not in task:
        task["needsUserAction"] = None
    if "userResolutions" not in task or not isinstance(task.get("userResolutions"), list):
        task["userResolutions"] = []
    else:
        normalized_res: List[Dict[str, Any]] = []
        for res in task["userResolutions"]:
            if isinstance(res, dict):
                normalized_res.append(
                    {
                        "question": coerce_task_text(res.get("question", "")),
                        "answer": coerce_task_text(res.get("answer", "")),
                        "timestamp": coerce_task_text(res.get("timestamp", "")),
                        "targetLane": coerce_task_text(res.get("targetLane", "")),
                    }
                )
        task["userResolutions"] = normalized_res
    if "needsUserCooldownUntilStep" not in task or not isinstance(
        task.get("needsUserCooldownUntilStep"), (int, float)
    ):
        task["needsUserCooldownUntilStep"] = None
    if "needsUserDuplicate" not in task:
        task["needsUserDuplicate"] = False
    if "lastNeedsUserReasonHash" not in task:
        task["lastNeedsUserReasonHash"] = None
    if "poRoundTrips" not in task or not isinstance(task.get("poRoundTrips"), (int, float)):
        task["poRoundTrips"] = 0
    if "stuckLoops" not in task or not isinstance(task.get("stuckLoops"), (int, float)):
        task["stuckLoops"] = 0
    for decision in task.get("decisions") or []:
        if isinstance(decision, dict):
            decision["summary"] = coerce_task_text(decision.get("summary"))
            decision["detail"] = coerce_task_text(decision.get("detail", ""))
            decision["agent"] = coerce_task_text(decision.get("agent", ""))
    for entry in task.get("transcript") or []:
        if isinstance(entry, dict):
            entry["content"] = coerce_task_text(entry.get("content"))
            entry["role"] = coerce_task_text(entry.get("role", ""))
            if entry.get("agent") is not None:
                entry["agent"] = coerce_task_text(entry.get("agent"))
    task["blockedBy"] = [str(b) for b in (task.get("blockedBy") or [])]
    if task.get("parentTaskId") is not None:
        task["parentTaskId"] = str(task["parentTaskId"])
    if "subtaskIds" in task:
        task["subtaskIds"] = [str(s) for s in (task.get("subtaskIds") or [])]
    elif task.get("parentTaskId"):
        task["subtaskIds"] = []
    if "executionOrder" in task:
        task["executionOrder"] = int(task["executionOrder"])
    if "subtaskSpawnCount" in task:
        task["subtaskSpawnCount"] = int(task["subtaskSpawnCount"])
    if "subtaskEscapeCount" in task:
        task["subtaskEscapeCount"] = int(task["subtaskEscapeCount"])
    if task.get("featureId") is not None:
        task["featureId"] = str(task["featureId"])
    if "featureHistory" not in task or not isinstance(task.get("featureHistory"), list):
        task["featureHistory"] = []
    else:
        normalized_hist: List[Dict[str, Any]] = []
        for entry in task["featureHistory"]:
            if isinstance(entry, dict):
                normalized_hist.append(
                    {
                        "timestamp": coerce_task_text(entry.get("timestamp", "")),
                        "source": coerce_task_text(entry.get("source", "")),
                        "requestTitle": coerce_task_text(entry.get("requestTitle", "")),
                        "requestBody": coerce_task_text(entry.get("requestBody", "")),
                        "poSummary": coerce_task_text(entry.get("poSummary", "")),
                        "childTaskId": coerce_task_text(entry.get("childTaskId", "")),
                    }
                )
        task["featureHistory"] = normalized_hist
    if "childTaskIds" in task:
        task["childTaskIds"] = [str(c) for c in (task.get("childTaskIds") or [])]
    elif task.get("workType") == "feature":
        task["childTaskIds"] = []
    wt = str(task.get("workType") or "implementation").lower()
    if wt not in ("planning", "implementation", "review", "qa", "user_action", "spike", "feature"):
        wt = "implementation"
    task["workType"] = wt
    if wt == "feature":
        task["requiresDev"] = False
        task["requiresQa"] = False
    elif "requiresDev" not in task:
        task["requiresDev"] = wt not in ("planning", "user_action")
    else:
        task["requiresDev"] = bool(task["requiresDev"])
    if "requiresQa" not in task:
        task["requiresQa"] = wt in ("implementation", "review")
    else:
        task["requiresQa"] = bool(task["requiresQa"])
    cb = str(task.get("createdBy") or "po").lower()
    task["createdBy"] = cb if cb in ("po", "user", "split") else "po"
    if task.get("lastDiagnosis") is not None and isinstance(task["lastDiagnosis"], dict):
        ld = task["lastDiagnosis"]
        task["lastDiagnosis"] = {
            "summary": coerce_task_text(ld.get("summary", "")),
            "problem": coerce_task_text(ld.get("problem", "")),
            "rootCause": coerce_task_text(ld.get("rootCause", "")),
            "evidence": [coerce_task_text(e) for e in (ld.get("evidence") or [])],
            "recommendedAction": coerce_task_text(ld.get("recommendedAction", "")),
            "suggestedAgent": coerce_task_text(ld.get("suggestedAgent", "")),
            **({"taskId": str(ld["taskId"])} if ld.get("taskId") else {}),
        }
    status = str(task.get("refinementStatus") or "pending")
    if status not in ("pending", "dev_reviewed", "po_updated", "ready", "blocked", "spike_pending"):
        status = "pending"
    if "refinementStatus" in task or str(task.get("status", "")) == "Refinement":
        task["refinementStatus"] = status
    if "refinementComplete" in task:
        task["refinementComplete"] = bool(task["refinementComplete"])
    if "refinementRoundTrips" in task:
        task["refinementRoundTrips"] = int(task["refinementRoundTrips"])
    elif str(task.get("status", "")) == "Refinement":
        task["refinementRoundTrips"] = 0
    if "refinementQuestions" in task:
        task["refinementQuestions"] = [
            coerce_task_text(q).strip() for q in task["refinementQuestions"] if coerce_task_text(q).strip()
        ]
    elif str(task.get("status", "")) == "Refinement":
        task["refinementQuestions"] = []
    if task.get("refinementNotes") is not None:
        task["refinementNotes"] = coerce_task_text(task["refinementNotes"])
    if "refinementDevReady" in task:
        task["refinementDevReady"] = bool(task["refinementDevReady"])
    if "needsSpike" in task:
        task["needsSpike"] = bool(task["needsSpike"])
    if task.get("spikeForTaskId") is not None:
        task["spikeForTaskId"] = str(task["spikeForTaskId"])
    spike_status = str(task.get("spikeStatus") or "").lower()
    if spike_status in ("pending", "running", "complete"):
        task["spikeStatus"] = spike_status
    if task.get("spikeObjective") is not None:
        task["spikeObjective"] = coerce_task_text(task["spikeObjective"])
    if task.get("spikeReport") is not None:
        task["spikeReport"] = coerce_task_text(task["spikeReport"])
    if "dependencyOutcomes" not in task or not isinstance(task.get("dependencyOutcomes"), list):
        task["dependencyOutcomes"] = []
    else:
        normalized_outcomes: List[Dict[str, Any]] = []
        for outcome in task["dependencyOutcomes"]:
            if not isinstance(outcome, dict):
                continue
            normalized_outcomes.append(
                {
                    "taskId": str(outcome.get("taskId") or ""),
                    "title": coerce_task_text(outcome.get("title") or ""),
                    "completedAt": coerce_task_text(outcome.get("completedAt") or ""),
                    "summary": coerce_task_text(outcome.get("summary") or ""),
                    "decisions": outcome.get("decisions") if isinstance(outcome.get("decisions"), list) else [],
                    "files": [str(f) for f in (outcome.get("files") or []) if f],
                    **(
                        {"refinementNotes": coerce_task_text(outcome["refinementNotes"])}
                        if outcome.get("refinementNotes")
                        else {}
                    ),
                    **(
                        {"spikeReport": coerce_task_text(outcome["spikeReport"])}
                        if outcome.get("spikeReport")
                        else {}
                    ),
                }
            )
        task["dependencyOutcomes"] = normalized_outcomes
    return task


def reset_refinement_fields(task: Dict[str, Any]) -> None:
    """Reset refinement state when a card enters the Refinement lane."""
    task["refinementStatus"] = "pending"
    task["refinementComplete"] = False
    task["refinementRoundTrips"] = 0
    task["refinementQuestions"] = []
    task["refinementNotes"] = None
    task["refinementDevReady"] = False


def init_refinement_fields(task: Dict[str, Any]) -> None:
    """Initialize refinement fields for new cards entering Refinement."""
    reset_refinement_fields(task)


def init_new_task(task: Dict[str, Any]) -> Dict[str, Any]:
    task.setdefault("status", "Backlog")
    task["files"] = []
    task["decisions"] = []
    task["transcript"] = []
    task.setdefault("acceptanceCriteria", [])
    task.setdefault("blockedBy", [])
    task.setdefault("subtaskIds", [])
    task.setdefault("priority", 100)
    task["qaFailure"] = None
    task["qaEvidence"] = None
    task["userQuestion"] = None
    task["poRoundTrips"] = 0
    task["stuckLoops"] = 0
    task["userResolutions"] = []
    task["needsUserCooldownUntilStep"] = None
    task["needsUserDuplicate"] = False
    task.setdefault("workType", "implementation")
    task.setdefault("requiresDev", True)
    task.setdefault("requiresQa", True)
    task.setdefault("createdBy", "po")
    if get_workflow_settings().get("requireBacklogRefinement") and task.get("status") == "Refinement":
        init_refinement_fields(task)
    return normalize_task(task)


def normalize_board_tasks() -> None:
    """Backfills context fields on tasks loaded from older saved projects."""
    for lane in state.SHARED_BOARD.values():
        for task in lane:
            normalize_task(task)
            sync_task_files_from_transcript(task)


def set_active_sprint_context(task_id: str, agent_role: str) -> None:
    state.ACTIVE_SPRINT_TASK_ID = task_id
    state.ACTIVE_SPRINT_AGENT = agent_role


def clear_active_sprint_context() -> None:
    state.ACTIVE_SPRINT_TASK_ID = None
    state.ACTIVE_SPRINT_AGENT = None
    state.REFINEMENT_MODE = False


def _now_timestamp() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def record_task_file(
    task_id: str,
    path: str,
    action: str = "written",
    *,
    persist: bool = False,
) -> None:
    task = find_task_by_id(task_id)
    if not task:
        return
    normalize_task(task)
    ts = _now_timestamp()
    existing = next((f for f in task["files"] if f.get("path") == path), None)
    if existing:
        existing["action"] = action
        existing["lastTouchedAt"] = ts
    else:
        task["files"].append({"path": path, "action": action, "lastTouchedAt": ts})
    if persist:
        from backend.services.board_service import publish_board_update
        from backend.services.project_service import save_current_project_state

        save_current_project_state()
        publish_board_update(task_id, source="task_files")


def sync_task_files_from_transcript(task: Dict[str, Any]) -> int:
    """Backfill task.files from file-tool transcript entries (success and failed)."""
    normalize_task(task)
    added = 0
    for entry in task.get("transcript") or []:
        if not isinstance(entry, dict):
            continue
        tool_name = entry.get("toolName")
        if tool_name not in FILE_TOOLS:
            continue
        tool_args = entry.get("toolArgs") or {}
        if not isinstance(tool_args, dict):
            continue
        path = file_path_from_tool(str(tool_name), tool_args)
        if not path:
            continue
        base_action = FILE_TOOLS[str(tool_name)]
        action = base_action if entry.get("toolSuccess") is not False else f"{base_action}-failed"
        paths = {f.get("path") for f in task["files"] if isinstance(f, dict)}
        if path in paths:
            continue
        ts = entry.get("timestamp") or _now_timestamp()
        task["files"].append({"path": path, "action": action, "lastTouchedAt": ts})
        added += 1
    return added


def record_task_git_commit(task_id: str, commit_info: Dict[str, Any]) -> None:
    task = find_task_by_id(task_id)
    if not task:
        return
    normalize_task(task)
    task["gitCommit"] = {
        "hash": coerce_task_text(commit_info.get("hash")),
        "message": coerce_task_text(commit_info.get("message", "")),
        "timestamp": commit_info.get("timestamp") or _now_timestamp(),
        **(
            {"remoteUrl": coerce_task_text(commit_info["remoteUrl"])}
            if commit_info.get("remoteUrl")
            else {}
        ),
    }
    publish_activity(
        task_id,
        "git_commit",
        f"Commit {task['gitCommit']['hash'][:8]}: {task['gitCommit']['message'][:120]}",
        role="system",
        agent="System",
    )


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
    **metadata: Any,
) -> None:
    task = find_task_by_id(task_id)
    if not task:
        return
    normalize_task(task)
    entry: Dict[str, Any] = {
        "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "role": role,
        "agent": agent or role,
        "content": content[:4000],
    }
    for key in ("toolName", "toolSuccess", "toolArgs", "toolOutput", "source"):
        if key in metadata and metadata[key] is not None:
            entry[key] = metadata[key]
    task["transcript"].append(entry)
    if len(task["transcript"]) > MAX_TASK_TRANSCRIPT:
        task["transcript"] = task["transcript"][-MAX_TASK_TRANSCRIPT:]
    activity_kind = "tool_failed" if metadata.get("toolSuccess") is False else "transcript"
    publish_activity(
        task_id,
        activity_kind,
        content,
        role=role,
        agent=agent or role,
    )


def clear_task_transcript(task_id: str) -> bool:
    task = find_task_by_id(task_id)
    if not task:
        return False
    normalize_task(task)
    task["transcript"] = []
    return True


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
    backlog.sort(
        key=lambda t: (
            0 if t.get("parentTaskId") else 1,
            t.get("executionOrder", 100),
            t.get("priority", 100),
            t.get("id", ""),
        )
    )


def sort_refinement() -> None:
    refinement = state.SHARED_BOARD.get("Refinement", [])
    refinement.sort(
        key=lambda t: (
            0 if t.get("workType") == "spike" else 1,
            t.get("executionOrder", 100),
            t.get("priority", 100),
            t.get("id", ""),
        )
    )


def create_spike_task(parent_task: Dict[str, Any], objective: str) -> Dict[str, Any]:
    """Create a linked spike card in Refinement for exploratory work."""
    from backend.services.board_service import publish_board_update

    objective_text = coerce_task_text(objective).strip() or "Technical exploration"
    parent_id = str(parent_task["id"])
    spike: Dict[str, Any] = {
        "title": f"Spike: {objective_text[:72]}",
        "description": objective_text,
        "acceptanceCriteria": ["Spike report JSON with findings and recommendations"],
        "workType": "spike",
        "requiresDev": True,
        "requiresQa": False,
        "spikeForTaskId": parent_id,
        "spikeStatus": "pending",
        "spikeObjective": objective_text,
        "status": "Refinement",
        "createdBy": "po",
    }
    init_new_task(spike)
    assign_unique_task_id(spike, existing_ids=all_task_ids())
    init_refinement_fields(spike)
    state.SHARED_BOARD.setdefault("Refinement", []).append(spike)

    parent = find_task_by_id(parent_id)
    if parent:
        parent["needsSpike"] = True
        parent["refinementStatus"] = "spike_pending"
        parent["refinementDevReady"] = False

    sort_refinement()
    publish_board_update(parent_id, "Refinement", source="spike_created")
    return spike


def next_spike_task() -> Optional[Dict[str, Any]]:
    """First pending spike card in Refinement."""
    ws = get_workflow_settings()
    if not ws.get("requireBacklogRefinement"):
        return None
    sort_refinement()
    for task in state.SHARED_BOARD.get("Refinement", []):
        normalize_task(task)
        if task.get("workType") != "spike":
            continue
        if str(task.get("spikeStatus") or "pending") == "complete":
            continue
        if task_dependencies_met(task):
            return task
    return None


def next_claimable_backlog_task() -> Optional[Dict[str, Any]]:
    sort_backlog()
    ws = get_workflow_settings()
    for task in state.SHARED_BOARD.get("Backlog", []):
        normalize_task(task)
        if not task.get("requiresDev", True):
            continue
        if task.get("workType") == "planning":
            continue
        if ws.get("requireBacklogRefinement") and task.get("refinementComplete") is False:
            continue
        if task_dependencies_met(task):
            return task
    return None


def count_claimable_backlog_tasks() -> int:
    """Count backlog cards eligible for claim (same rules as next_claimable_backlog_task)."""
    sort_backlog()
    ws = get_workflow_settings()
    count = 0
    for task in state.SHARED_BOARD.get("Backlog", []):
        normalize_task(task)
        if not task.get("requiresDev", True):
            continue
        if task.get("workType") == "planning":
            continue
        if ws.get("requireBacklogRefinement") and task.get("refinementComplete") is False:
            continue
        if task_dependencies_met(task):
            count += 1
    return count


def next_refinement_task() -> Optional[Dict[str, Any]]:
    """First claimable task in Refinement lane."""
    ws = get_workflow_settings()
    if not ws.get("requireBacklogRefinement"):
        return None
    for task in state.SHARED_BOARD.get("Refinement", []):
        normalize_task(task)
        if task.get("workType") == "spike":
            continue
        if task.get("refinementStatus") == "spike_pending":
            continue
        if not task.get("requiresDev", True):
            continue
        if task.get("workType") == "planning":
            continue
        if task.get("refinementComplete"):
            continue
        if task_dependencies_met(task):
            return task
    return None


def next_po_planning_backlog_task() -> Optional[Dict[str, Any]]:
    """First backlog task that needs PO (planning / no dev work)."""
    sort_backlog()
    for task in state.SHARED_BOARD.get("Backlog", []):
        normalize_task(task)
        if task.get("requiresDev", True) and task.get("workType") != "planning":
            continue
        if task_dependencies_met(task):
            return task
    return None


MAX_DEPENDENCY_OUTCOMES = 20


def validate_blocked_by(task: Dict[str, Any]) -> List[str]:
    """Blocker IDs that are not on the board (task can never unblock)."""
    normalize_task(task)
    known = all_task_ids()
    missing: List[str] = []
    for blocker_id in task.get("blockedBy") or []:
        bid = str(blocker_id)
        if bid and bid not in known:
            missing.append(bid)
    return missing


def build_dependency_outcome(completed_task: Dict[str, Any]) -> Dict[str, Any]:
    """Compact rollup from a completed blocker/subtask for parent prompts."""
    normalize_task(completed_task)
    last_decisions: List[Dict[str, str]] = []
    for decision in (completed_task.get("decisions") or [])[-3:]:
        if isinstance(decision, dict):
            last_decisions.append(
                {
                    "agent": coerce_task_text(decision.get("agent", "")),
                    "type": coerce_task_text(decision.get("type", "")),
                    "summary": coerce_task_text(decision.get("summary", ""))[:300],
                }
            )
    file_paths: List[str] = []
    for entry in (completed_task.get("files") or [])[-8:]:
        if isinstance(entry, dict):
            path = str(entry.get("path") or "")
        else:
            path = str(entry)
        if path:
            file_paths.append(path)
    summary = ""
    for decision in reversed(completed_task.get("decisions") or []):
        if isinstance(decision, dict) and decision.get("summary"):
            summary = coerce_task_text(decision["summary"])[:400]
            break
    if not summary:
        summary = coerce_task_text(completed_task.get("description") or "")[:400]
    if not summary:
        summary = f"Completed: {completed_task.get('title', completed_task.get('id'))}"
    notes = coerce_task_text(completed_task.get("refinementNotes") or "")
    spike = coerce_task_text(completed_task.get("spikeReport") or "")
    return {
        "taskId": str(completed_task.get("id", "")),
        "title": coerce_task_text(completed_task.get("title", "")),
        "completedAt": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "summary": summary,
        "decisions": last_decisions,
        "files": file_paths[:8],
        **({"refinementNotes": notes[:500]} if notes else {}),
        **({"spikeReport": spike[:500]} if spike else {}),
    }


def append_dependency_outcome(task: Dict[str, Any], outcome: Dict[str, Any]) -> None:
    normalize_task(task)
    outcomes = list(task.get("dependencyOutcomes") or [])
    outcome_id = str(outcome.get("taskId") or "")
    outcomes = [o for o in outcomes if str(o.get("taskId") or "") != outcome_id]
    outcomes.append(outcome)
    if len(outcomes) > MAX_DEPENDENCY_OUTCOMES:
        outcomes = outcomes[-MAX_DEPENDENCY_OUTCOMES:]
    task["dependencyOutcomes"] = outcomes


def on_task_completed(task_id: str) -> None:
    """Roll dependency outcomes to dependents when a card reaches Done."""
    completed = find_task_by_id(task_id)
    if not completed:
        return
    outcome = build_dependency_outcome(completed)
    needle = str(task_id)
    for lane_tasks in state.SHARED_BOARD.values():
        for task in lane_tasks:
            if str(task.get("id", "")) == needle:
                continue
            normalize_task(task)
            blocked = [str(b) for b in (task.get("blockedBy") or [])]
            is_parent = str(task.get("parentTaskId") or "") == needle
            if needle in blocked or is_parent:
                append_dependency_outcome(task, outcome)
    from backend.services.subtask_service import on_subtask_completed

    on_subtask_completed(task_id)
    completed_norm = find_task_by_id(task_id)
    if completed_norm and completed_norm.get("featureId"):
        from backend.services.feature_service import rollup_child_to_feature

        rollup_child_to_feature(task_id)


def _format_older_decisions_block(decisions: List[Dict[str, Any]]) -> str:
    if len(decisions) <= 8:
        return ""
    older = decisions[:-8]
    lines = [
        f"- [{d.get('timestamp', '?')}] {d.get('agent', 'Agent')} ({d.get('type', 'note')}): "
        f"{coerce_task_text(d.get('summary', ''))[:200]}"
        for d in older[-25:]
        if isinstance(d, dict)
    ]
    if not lines:
        return ""
    return (
        "\n=== EARLIER DECISIONS (condensed) ===\n"
        + "\n".join(lines)
        + f"\n({len(older)} earlier decision(s) summarized above; last 8 shown in full below.)\n"
    )


def _format_older_resolutions_block(resolutions: List[Dict[str, Any]]) -> str:
    if len(resolutions) <= 5:
        return ""
    older = resolutions[:-5]
    lines = [
        f"- Q: {coerce_task_text(r.get('question', ''))[:180]}\n  A: {coerce_task_text(r.get('answer', ''))[:220]}"
        for r in older[-15:]
        if isinstance(r, dict)
    ]
    if not lines:
        return ""
    return (
        "\n=== EARLIER USER ANSWERS (condensed) ===\n"
        + "\n".join(lines)
        + f"\n({len(older)} earlier answer(s) summarized above; last 5 shown in full below.)\n"
    )


def build_dod_block() -> str:
    settings = get_workflow_settings()
    dod = settings.get("definitionOfDone") or []
    if not dod:
        return ""
    lines = "\n".join(f"- {item}" for item in dod)
    return f"\n=== DEFINITION OF DONE (project) ===\n{lines}\n"


def build_task_prompt(task: Dict[str, Any], brief: str) -> str:
    """Builds a structured prompt for sprint agents."""
    from backend.services.prompt_budget import truncate_brief, workspace_file_list_cap
    from backend.services.workflow_settings import get_workflow_settings

    num_ctx = int(get_workflow_settings().get("ollamaNumCtx", 32768))
    brief = truncate_brief(brief, num_ctx)

    normalize_task(task)
    paths = sorted(state.VIRTUAL_FILESYSTEM.keys())
    cap = workspace_file_list_cap(num_ctx)
    if len(paths) > cap:
        file_list = ", ".join(paths[:cap]) + f", … (+{len(paths) - cap} more)"
    else:
        file_list = ", ".join(paths) or "(empty workspace)"
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

    subtask_extra = ""
    subtasks = task.get("subtaskIds") or []
    if subtasks:
        sub_lines = []
        for sid in subtasks:
            sub = find_task_by_id(str(sid))
            if sub:
                done = is_task_done(str(sid))
                sub_lines.append(
                    f"- {sid}: {sub.get('title', '?')} (order {sub.get('executionOrder', '?')}, "
                    f"{'Done' if done else sub.get('status', '?')})"
                )
            else:
                sub_lines.append(f"- {sid}: (missing)")
        subtask_extra = (
            "\n=== SUBTASKS (must all reach Done before this card completes) ===\n"
            + "\n".join(sub_lines)
            + "\n"
        )
    if task.get("parentTaskId"):
        subtask_extra += f"\nParent todo: {task['parentTaskId']}\n"
    if task.get("featureId"):
        from backend.services.feature_service import build_feature_context_block

        subtask_extra += build_feature_context_block(str(task["featureId"]))

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
        f"{subtask_extra}"
    )

    qa_fail = task.get("qaFailure")
    if qa_fail:
        prompt += (
            "\n=== LAST QA FAILURE ===\n"
            f"Reason: {qa_fail.get('reason', '')}\n"
            f"Output: {qa_fail.get('output', '')[:500]}\n"
            f"When: {qa_fail.get('timestamp', '')}\n"
        )

    dependency_outcomes = task.get("dependencyOutcomes") or []
    if dependency_outcomes:
        prompt += "\n=== COMPLETED DEPENDENCY OUTCOMES ===\n"
        for outcome in dependency_outcomes[-10:]:
            if not isinstance(outcome, dict):
                continue
            prompt += (
                f"\n[{outcome.get('taskId')}] {outcome.get('title', '?')} "
                f"(done {outcome.get('completedAt', '?')})\n"
                f"Summary: {outcome.get('summary', '')}\n"
            )
            if outcome.get("refinementNotes"):
                prompt += f"Refinement notes: {outcome['refinementNotes'][:400]}\n"
            if outcome.get("spikeReport"):
                prompt += f"Spike report: {outcome['spikeReport'][:400]}\n"
            files = outcome.get("files") or []
            if files:
                prompt += f"Key files: {', '.join(str(f) for f in files[:6])}\n"
            for decision in (outcome.get("decisions") or [])[:2]:
                if isinstance(decision, dict):
                    prompt += f"  - {decision.get('agent', '?')}: {decision.get('summary', '')}\n"
        prompt += "Use these completed dependency results — do not redo finished blocker work.\n"

    related_ids = [str(r) for r in (task.get("relatedTaskIds") or []) if r][:5]
    if related_ids:
        related_blocks: List[str] = []
        for rid in related_ids:
            related = find_task_by_id(rid)
            if not related:
                continue
            normalize_task(related)
            done = is_task_done(rid)
            has_useful = bool(related.get("decisions") or related.get("files") or done)
            if not has_useful and not done:
                # Still surface in-flight same-request cards briefly
                related_blocks.append(
                    f"[{rid}] {related.get('title', '?')} — status {related.get('status', '?')} "
                    "(in flight; reuse, do not recreate)"
                )
                continue
            outcome = build_dependency_outcome(related)
            status_label = "Done" if done else str(related.get("status") or "in flight")
            block = (
                f"[{rid}] {outcome.get('title', '?')} ({status_label})\n"
                f"Summary: {outcome.get('summary', '')}\n"
            )
            files = outcome.get("files") or []
            if files:
                block += f"Key files: {', '.join(str(f) for f in files[:6])}\n"
            for decision in (outcome.get("decisions") or [])[:2]:
                if isinstance(decision, dict):
                    block += f"  - {decision.get('agent', '?')}: {decision.get('summary', '')}\n"
            related_blocks.append(block)
        if related_blocks:
            prompt += (
                "\n=== RELATED WORK (reuse — do not redo) ===\n"
                "Related work already done / in flight — reuse outputs, do not recreate the same request.\n"
                + "\n".join(related_blocks)
                + "\n"
            )

    resolutions = task.get("userResolutions") or []
    older_res_block = _format_older_resolutions_block(resolutions)
    if older_res_block:
        prompt += older_res_block

    if resolutions:
        prompt += "\n=== PRIOR USER ANSWERS (do not re-ask) ===\n"
        for res in resolutions[-5:]:
            if not isinstance(res, dict):
                continue
            prompt += (
                f"Q: {res.get('question', '')[:300]}\n"
                f"A: {res.get('answer', '')[:400]}\n"
                f"(→ {res.get('targetLane', '?')} at {res.get('timestamp', '?')})\n\n"
            )
        prompt += "These questions were already answered — do not escalate again for the same topic.\n"

    if task.get("userQuestion"):
        prompt += f"\n=== USER QUESTION PENDING ===\n{task['userQuestion']}\n"

    notes = coerce_task_text(task.get("refinementNotes") or "")
    if notes:
        prompt += f"\n=== REFINEMENT NOTES ===\n{notes[:2000]}\n"
    spike_report = coerce_task_text(task.get("spikeReport") or "")
    if spike_report:
        prompt += f"\n=== SPIKE REPORT ===\n{spike_report[:2000]}\n"

    decisions = task.get("decisions") or []
    older_dec_block = _format_older_decisions_block(decisions)
    if older_dec_block:
        prompt += older_dec_block

    if decisions:
        prompt += "\n=== PRIOR AGENT DECISIONS ON THIS CARD ===\n"
        for d in decisions[-8:]:
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
