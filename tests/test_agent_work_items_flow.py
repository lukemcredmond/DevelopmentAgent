"""Agent work-item derivation and task flow API."""

from __future__ import annotations

from unittest.mock import patch

from backend.agents.task_context import init_new_task, normalize_task
from backend.bootstrap import initialize
from backend.services.agent_work_items import (
    derive_agent_work_items,
    primary_work_item_id,
    refresh_agent_work_items,
    suggested_focus_work_item_id,
)
from backend.services.task_flow import build_task_flow, build_task_flow_summary


def test_derive_pending_then_done_after_write_and_verify():
    initialize()
    from backend import state

    task = init_new_task({"id": "T-AW", "title": "Work", "description": "d", "status": "In Progress"})
    state.SHARED_BOARD = {"In Progress": [task]}
    items = derive_agent_work_items(task)
    by_id = {i["id"]: i for i in items}
    assert by_id["read:files"]["status"] == "pending"
    assert by_id["write:implement"]["status"] == "pending"

    task["files"] = [{"path": "a.ts", "action": "read"}, {"path": "a.ts", "action": "written"}]
    task["transcript"] = [
        {"toolName": "write_file", "toolSuccess": True, "content": "ok"},
        {"toolName": "run_command", "toolSuccess": True, "content": "ok", "toolArgs": {"command": "npm test"}},
    ]
    items2 = derive_agent_work_items(task)
    by2 = {i["id"]: i for i in items2}
    assert by2["read:files"]["status"] == "done"
    assert by2["write:implement"]["status"] == "done"
    assert by2["verify:command"]["status"] == "done"


def test_blocked_fingerprint_adds_blocked_item():
    initialize()
    from backend import state
    from backend.agents.tool_fingerprints import block_tool_fingerprint_on_task

    task = init_new_task({"id": "T-BL", "title": "t", "description": "d"})
    state.SHARED_BOARD = {"In Progress": [task]}
    block_tool_fingerprint_on_task(task, "run_command", {"command": "flutter analyze"})
    items = derive_agent_work_items(task)
    assert any(i["id"] == "blocked:tools" and i["status"] == "blocked" for i in items)


def test_normalize_persists_agent_work_items():
    initialize()
    from backend import state

    task = init_new_task({"id": "T-NM", "title": "t", "description": "d"})
    task["files"] = [{"path": "x.py", "action": "written"}]
    state.SHARED_BOARD = {"In Progress": [task]}
    normalize_task(task)
    assert isinstance(task.get("agentWorkItems"), list)
    assert len(task["agentWorkItems"]) >= 3


def test_derive_includes_flow_match_rules():
    initialize()
    from backend import state

    task = init_new_task({"id": "T-FM", "title": "t", "description": "d"})
    state.SHARED_BOARD = {"In Progress": [task]}
    items = derive_agent_work_items(task)
    by_id = {i["id"]: i for i in items}
    assert "read_file" in by_id["read:files"]["flowMatch"]["toolNames"]
    assert "write_file" in by_id["write:implement"]["flowMatch"]["toolNames"]
    assert "apply_patch" in by_id["write:implement"]["flowMatch"]["toolNames"]
    assert "run_command" in by_id["verify:command"]["flowMatch"]["toolNames"]
    assert "lane" in by_id["lane:advance"]["flowMatch"]["tags"]


def test_build_task_flow_attaches_work_item_ids():
    initialize()
    from backend import state

    task = init_new_task({"id": "T-FLOW", "title": "Flow card", "description": "d"})
    state.SHARED_BOARD = {"In Progress": [task]}
    state.LLM_DEBUG_LOG.clear()
    state.TOOL_EXECUTION_LOG.clear()
    state.LLM_DEBUG_LOG.append(
        {
            "id": "llm1",
            "timestamp": "2026-01-01 10:00:00",
            "taskId": "T-FLOW",
            "agent": "Developer",
            "iteration": 1,
            "requestMessages": [{"role": "user", "content": "hello prompt"}],
            "responseContent": "hi",
            "responseToolCalls": [{"name": "read_file"}],
            "toolNames": ["read_file"],
        }
    )
    state.LLM_DEBUG_LOG.append(
        {
            "id": "llm2",
            "timestamp": "2026-01-01 10:01:00",
            "taskId": "OTHER",
            "agent": "Developer",
            "iteration": 1,
            "requestMessages": [],
            "responseContent": "other",
        }
    )
    state.TOOL_EXECUTION_LOG.append(
        {
            "eventId": "ev1",
            "timestamp": "2026-01-01 10:00:05",
            "taskId": "T-FLOW",
            "toolName": "read_file",
            "toolArgs": {"path": "a.ts"},
            "toolOutput": "file body " * 100,
            "toolSuccess": True,
        }
    )
    state.TOOL_EXECUTION_LOG.append(
        {
            "eventId": "ev2",
            "timestamp": "2026-01-01 10:00:10",
            "taskId": "T-FLOW",
            "toolName": "write_file",
            "toolArgs": {"path": "a.ts"},
            "toolOutput": "ok",
            "toolSuccess": True,
        }
    )
    with patch("backend.services.task_flow.list_step_traces_for_task", return_value=[]):
        flow = build_task_flow("T-FLOW", limit=40, include_full=True)
    assert flow["taskId"] == "T-FLOW"
    assert all(n.get("taskId") == "T-FLOW" for n in flow["nodes"])
    tool_read = next(n for n in flow["nodes"] if n.get("toolName") == "read_file")
    tool_write = next(n for n in flow["nodes"] if n.get("toolName") == "write_file")
    assert "read:files" in (tool_read.get("workItemIds") or [])
    assert "write:implement" in (tool_write.get("workItemIds") or [])
    assert "read:files" not in (tool_write.get("workItemIds") or [])
    assert "read:files" in flow["workItemIndex"]
    assert tool_read["id"] in flow["workItemIndex"]["read:files"]["nodeIds"]
    llm = next(n for n in flow["nodes"] if n["kind"] == "llm")
    assert "read:files" in (llm.get("workItemIds") or [])
    assert llm.get("primaryWorkItemId") == "read:files"
    assert flow.get("suggestedFocusWorkItemId") == "read:files"


def test_primary_work_item_prefers_earlier_checklist_row():
    initialize()
    from backend import state

    task = init_new_task({"id": "T-PRIM", "title": "t", "description": "d"})
    state.SHARED_BOARD = {"In Progress": [task]}
    items = derive_agent_work_items(task)
    matched = ["write:implement", "read:files"]
    assert primary_work_item_id(items, matched) == "read:files"


def test_suggested_focus_pending_before_blocked():
    initialize()
    from backend import state
    from backend.agents.tool_fingerprints import block_tool_fingerprint_on_task

    task = init_new_task({"id": "T-FOC", "title": "t", "description": "d"})
    state.SHARED_BOARD = {"In Progress": [task]}
    items = derive_agent_work_items(task)
    assert suggested_focus_work_item_id(items) == "read:files"
    block_tool_fingerprint_on_task(task, "run_command", {"command": "x"})
    items2 = derive_agent_work_items(task)
    assert suggested_focus_work_item_id(items2) == "read:files"


def test_flow_summary_includes_suggested_focus():
    initialize()
    from backend import state

    task = init_new_task({"id": "T-SUM", "title": "t", "description": "d"})
    state.SHARED_BOARD = {"In Progress": [task]}
    state.LLM_DEBUG_LOG.clear()
    state.TOOL_EXECUTION_LOG.clear()
    with patch("backend.services.task_flow.list_step_traces_for_task", return_value=[]):
        summary = build_task_flow_summary("T-SUM", limit=40)
    assert summary.get("suggestedFocusWorkItemId") == "read:files"


def test_build_task_flow_filters_by_task_id():
    initialize()
    from backend import state

    task = init_new_task({"id": "T-FLOW", "title": "Flow card", "description": "d"})
    state.SHARED_BOARD = {"In Progress": [task]}
    state.LLM_DEBUG_LOG.clear()
    state.TOOL_EXECUTION_LOG.clear()
    state.LLM_DEBUG_LOG.append(
        {
            "id": "llm1",
            "timestamp": "2026-01-01 10:00:00",
            "taskId": "T-FLOW",
            "agent": "Developer",
            "iteration": 1,
            "requestMessages": [{"role": "user", "content": "hello prompt"}],
            "responseContent": "hi",
            "responseToolCalls": [{"name": "read_file"}],
        }
    )
    state.LLM_DEBUG_LOG.append(
        {
            "id": "llm2",
            "timestamp": "2026-01-01 10:01:00",
            "taskId": "OTHER",
            "agent": "Developer",
            "iteration": 1,
            "requestMessages": [],
            "responseContent": "other",
        }
    )
    state.TOOL_EXECUTION_LOG.append(
        {
            "eventId": "ev1",
            "timestamp": "2026-01-01 10:00:05",
            "taskId": "T-FLOW",
            "toolName": "read_file",
            "toolArgs": {"path": "a.ts"},
            "toolOutput": "file body " * 100,
            "toolSuccess": True,
        }
    )
    with patch("backend.services.task_flow.list_step_traces_for_task", return_value=[]):
        flow = build_task_flow("T-FLOW", limit=40, include_full=True)
    assert flow["taskId"] == "T-FLOW"
    assert all(n.get("taskId") == "T-FLOW" for n in flow["nodes"])
    llm = next(n for n in flow["nodes"] if n["kind"] == "llm")
    assert "hello prompt" in str(llm.get("requestMessages"))
    tool = next(n for n in flow["nodes"] if n["kind"] == "tool")
    assert tool["toolName"] == "read_file"
    assert "file body" in tool["toolOutput"]


def test_refresh_includes_in_card_snapshot():
    initialize()
    from backend import state
    from backend.services.step_diagnostics import build_card_work_snapshot

    task = init_new_task({"id": "T-SNAP", "title": "t", "description": "d"})
    task["files"] = [{"path": "a.ts", "action": "read"}]
    state.SHARED_BOARD = {"In Progress": [task]}
    snap = build_card_work_snapshot(task)
    assert snap.get("agentWorkItems")
    assert any(i.get("id") == "read:files" and i.get("status") == "done" for i in snap["agentWorkItems"])
