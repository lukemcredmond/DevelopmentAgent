"""Derived agent work-item checklist (separate from QA acceptance criteria)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

AGENT_WORK_ITEMS_MAX = 12
_VALID_STATUS = frozenset({"pending", "done", "blocked"})

# Stable tool → work-item match rules (also used by task_flow annotation).
FLOW_MATCH_BY_ID: Dict[str, Dict[str, Any]] = {
    "read:files": {
        "toolNames": ["read_file", "list_dir", "glob_file_search", "grep"],
        "tags": ["read"],
    },
    "write:implement": {
        "toolNames": ["write_file", "apply_patch"],
        "tags": ["write"],
    },
    "verify:command": {
        "toolNames": ["run_command", "run_test"],
        "tags": ["verify"],
    },
    "lane:advance": {
        "toolNames": ["update_board"],
        "stopReasons": [
            "duplicate_tool",
            "tool_failure_stop",
            "read_only_no_edits",
            "plan_exhausted",
            "max_iterations",
            "step_timeout",
            "completed_text_only",
            "text_only",
            "no_writes",
        ],
        "tags": ["lane"],
    },
    "blocked:tools": {
        "stopReasons": ["duplicate_tool", "tool_failure_stop"],
        "tags": ["blocked"],
    },
    "subtasks:children": {
        "tags": ["subtasks"],
    },
}


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _flow_match_for(item_id: str, *, blocked_tool_names: Optional[List[str]] = None) -> Dict[str, Any]:
    if item_id.startswith("gate:"):
        return {"tags": ["gate"], "toolNames": ["update_board"]}
    base = dict(FLOW_MATCH_BY_ID.get(item_id) or {"tags": []})
    if item_id == "blocked:tools" and blocked_tool_names:
        base = {**base, "toolNames": list(dict.fromkeys(blocked_tool_names))}
    return base


def _item(
    item_id: str,
    label: str,
    status: str,
    *,
    agent_role: Optional[str] = None,
    flow_match: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    st = status if status in _VALID_STATUS else "pending"
    out: Dict[str, Any] = {
        "id": item_id,
        "label": label,
        "status": st,
        "source": "derived",
        "updatedAt": _now(),
        "flowMatch": flow_match if flow_match is not None else _flow_match_for(item_id),
    }
    if agent_role:
        out["agentRole"] = agent_role
    return out


def _file_actions(task: Dict[str, Any]) -> set[str]:
    actions: set[str] = set()
    for f in task.get("files") or []:
        if isinstance(f, dict):
            actions.add(str(f.get("action") or "touched").lower())
        elif isinstance(f, str) and f.strip():
            actions.add("touched")
    return actions


def _transcript_tool_success(task: Dict[str, Any], names: set[str]) -> bool:
    for entry in task.get("transcript") or []:
        if not isinstance(entry, dict):
            continue
        if str(entry.get("toolName") or "") not in names:
            continue
        if entry.get("toolSuccess") is False:
            continue
        return True
    return False


def _has_verify_after_write(task: Dict[str, Any]) -> bool:
    """True if a successful run_command/run_test appears after a write in transcript."""
    saw_write = False
    for entry in task.get("transcript") or []:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("toolName") or "")
        ok = entry.get("toolSuccess") is not False
        if name in ("write_file", "apply_patch") and ok:
            saw_write = True
        elif saw_write and name in ("run_command", "run_test") and ok:
            return True
    # Fallback: tested file action or successful verify without strict order
    actions = _file_actions(task)
    if "written" in actions and ("tested" in actions or _transcript_tool_success(task, {"run_command", "run_test"})):
        return True
    return False


def _blocked_tool_names(task: Dict[str, Any]) -> List[str]:
    names: List[str] = []
    for entry in task.get("blockedToolFingerprints") or []:
        if isinstance(entry, dict) and entry.get("tool"):
            names.append(str(entry["tool"]))
    return list(dict.fromkeys(names))


def derive_agent_work_items(task: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Build honest done/pending/blocked checklist from card signals (not AC)."""
    from backend.agents.task_context import get_task_lane, is_task_done
    from backend.services.step_diagnostics import gates_remaining_for_lane

    task_id = str(task.get("id") or "")
    lane = get_task_lane(task_id) if task_id else None
    lane = lane or str(task.get("status") or "")
    actions = _file_actions(task)
    has_read = bool(actions & {"read", "context"}) or _transcript_tool_success(task, {"read_file"})
    has_write = "written" in actions or _transcript_tool_success(task, {"write_file", "apply_patch"})
    has_verify = _has_verify_after_write(task) if has_write else _transcript_tool_success(
        task, {"run_command", "run_test"}
    )

    outcome = task.get("lastStepOutcome") if isinstance(task.get("lastStepOutcome"), dict) else {}
    stop = str(outcome.get("stopReason") or outcome.get("exitReason") or "").lower()
    stuck_stop = stop in (
        "duplicate_tool",
        "tool_failure_stop",
        "read_only_no_edits",
        "plan_exhausted",
        "max_iterations",
        "step_timeout",
        "completed_text_only",
        "text_only",
        "no_writes",
    )
    stuck_loops = int(task.get("stuckLoops") or 0)
    lane_after = str(outcome.get("laneAfter") or "")
    lane_before = str(outcome.get("laneBefore") or "")
    moved = bool(lane_before and lane_after and lane_before != lane_after)

    items: List[Dict[str, Any]] = []

    items.append(
        _item(
            "read:files",
            "Read related files",
            "done" if has_read else "pending",
            agent_role="Developer",
        )
    )
    items.append(
        _item(
            "write:implement",
            "Implement / edit files",
            "done" if has_write else "pending",
            agent_role="Developer",
        )
    )
    verify_status = "pending"
    if has_verify:
        verify_status = "done"
    elif has_write and not has_verify:
        verify_status = "pending"
    items.append(
        _item(
            "verify:command",
            "Verify (command / test)",
            verify_status,
            agent_role="Developer",
        )
    )

    if stuck_stop or stuck_loops > 0:
        move_status = "blocked"
        move_label = "Advance past current lane (stuck — change approach)"
    elif moved or lane in ("Code Review", "QA", "Done"):
        move_status = "done"
        move_label = "Advance past current lane"
    else:
        move_status = "pending"
        move_label = "Advance past current lane"
    items.append(_item("lane:advance", move_label, move_status))

    for gate in gates_remaining_for_lane(lane)[:4]:
        items.append(
            _item(
                f"gate:{gate}",
                f"Pipeline: reach {gate}",
                "pending",
            )
        )

    subtask_ids = [str(s) for s in (task.get("subtaskIds") or []) if s]
    if subtask_ids:
        done_n = sum(1 for sid in subtask_ids if is_task_done(sid))
        total = len(subtask_ids)
        items.append(
            _item(
                "subtasks:children",
                f"Subtasks done ({done_n}/{total})",
                "done" if done_n >= total else "pending",
            )
        )

    blocked_fps = task.get("blockedToolFingerprints") or []
    if isinstance(blocked_fps, list) and blocked_fps:
        labels = []
        for entry in blocked_fps[-3:]:
            if isinstance(entry, dict) and entry.get("label"):
                labels.append(str(entry["label"]))
        bit = "; ".join(labels)[:120] if labels else "identical tool args"
        blocked_names = _blocked_tool_names(task)
        items.append(
            _item(
                "blocked:tools",
                f"Change approach (blocked tools: {bit})",
                "blocked",
                flow_match=_flow_match_for("blocked:tools", blocked_tool_names=blocked_names),
            )
        )

    return items[:AGENT_WORK_ITEMS_MAX]


def _normalize_flow_match(raw: Any) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    out: Dict[str, Any] = {}
    tools = raw.get("toolNames")
    if isinstance(tools, list):
        out["toolNames"] = [str(t) for t in tools if t]
    stops = raw.get("stopReasons")
    if isinstance(stops, list):
        out["stopReasons"] = [str(s) for s in stops if s]
    tags = raw.get("tags")
    if isinstance(tags, list):
        out["tags"] = [str(t) for t in tags if t]
    return out


def normalize_agent_work_items(raw: Any) -> List[Dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    out: List[Dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        status = str(item.get("status") or "pending")
        if status not in _VALID_STATUS:
            status = "pending"
        item_id = str(item["id"])
        match = _normalize_flow_match(item.get("flowMatch"))
        if not match:
            match = _flow_match_for(item_id)
        entry: Dict[str, Any] = {
            "id": item_id,
            "label": str(item.get("label") or item_id),
            "status": status,
            "source": "derived",
            "updatedAt": str(item.get("updatedAt") or ""),
            "flowMatch": match,
        }
        if item.get("agentRole"):
            entry["agentRole"] = str(item["agentRole"])
        out.append(entry)
    return out[:AGENT_WORK_ITEMS_MAX]


def refresh_agent_work_items(task: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Derive and persist agentWorkItems on the task dict."""
    items = derive_agent_work_items(task)
    task["agentWorkItems"] = items
    return items


def work_item_ids_for_tool_name(
    items: List[Dict[str, Any]],
    tool_name: str,
    *,
    tool_args: Optional[Dict[str, Any]] = None,
) -> List[str]:
    """Return work-item ids whose flowMatch.toolNames include this tool."""
    name = str(tool_name or "")
    if not name:
        return []
    matched: List[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        fm = item.get("flowMatch") if isinstance(item.get("flowMatch"), dict) else {}
        tools = fm.get("toolNames") or []
        if name in tools:
            matched.append(str(item["id"]))
            continue
        # Blocked fingerprint: match tool name from label when toolNames empty-ish
        if item.get("id") == "blocked:tools" and name:
            label = str(item.get("label") or "")
            if name in label:
                matched.append(str(item["id"]))
    return list(dict.fromkeys(matched))


def work_item_ids_for_llm_tools(items: List[Dict[str, Any]], tool_names: List[str]) -> List[str]:
    ids: List[str] = []
    for name in tool_names:
        ids.extend(work_item_ids_for_tool_name(items, name))
    return list(dict.fromkeys(ids))
