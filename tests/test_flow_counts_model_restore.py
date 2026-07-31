"""Per-work-item flow counts, duplicate-skip logging, and primary model restore."""

from __future__ import annotations

from unittest.mock import patch

from backend.agents.task_context import init_new_task
from backend.bootstrap import initialize
from backend.services.agent_work_items import derive_agent_work_items, work_item_ids_for_node
from backend.services.task_flow import build_task_flow, build_task_flow_summary


def _tool_event(event_id: str, ts: str, tool_name: str, task_id: str, **extra):
    return {
        "eventId": event_id,
        "timestamp": ts,
        "taskId": task_id,
        "toolName": tool_name,
        "toolArgs": {"path": "a.ts"},
        "toolOutput": "ok",
        "toolSuccess": True,
        "durationMs": 100,
        **extra,
    }


def _seed_card(task_id: str = "T-CNT"):
    from backend import state

    task = init_new_task({"id": task_id, "title": "Counts", "description": "d"})
    state.SHARED_BOARD = {"In Progress": [task]}
    state.LLM_DEBUG_LOG.clear()
    state.TOOL_EXECUTION_LOG.clear()
    return task


def test_flow_summary_counts_per_work_item():
    initialize()
    from backend import state

    _seed_card()
    state.LLM_DEBUG_LOG.append(
        {
            "id": "llm1",
            "timestamp": "2026-01-01 10:00:00",
            "taskId": "T-CNT",
            "agent": "Developer",
            "iteration": 1,
            "durationMs": 2000,
            "requestMessages": [{"role": "user", "content": "p"}],
            "responseContent": "r",
            "toolNames": ["read_file"],
        }
    )
    state.LLM_DEBUG_LOG.append(
        {
            "id": "llm-other",
            "timestamp": "2026-01-01 10:00:01",
            "taskId": "OTHER-CARD",
            "agent": "Developer",
            "toolNames": ["read_file"],
        }
    )
    state.TOOL_EXECUTION_LOG.extend(
        [
            _tool_event("ev1", "2026-01-01 10:00:05", "read_file", "T-CNT"),
            _tool_event("ev2", "2026-01-01 10:00:06", "read_file", "T-CNT"),
            _tool_event("ev3", "2026-01-01 10:00:07", "write_file", "T-CNT"),
            _tool_event("ev4", "2026-01-01 10:00:08", "read_file", "OTHER-CARD"),
            _tool_event("ev5", "2026-01-01 10:00:09", "run_command", "T-CNT", toolSuccess=False),
        ]
    )
    with patch("backend.services.task_flow.list_step_traces_for_task", return_value=[]):
        summary = build_task_flow_summary("T-CNT", limit=40)

    read = summary["workItemIndex"]["read:files"]
    assert read["llmCalls"] == 1  # only the T-CNT LLM turn that asked for read_file
    assert read["toolCalls"] == 2  # OTHER-CARD read_file excluded
    assert read["toolCounts"] == {"read_file": 2}
    assert read["llmMs"] == 2000
    assert read["toolMs"] == 200
    assert read["firstAt"] == "2026-01-01 10:00:00"
    assert summary["workItemIndex"]["write:implement"]["toolCounts"] == {"write_file": 1}
    assert summary["workItemIndex"]["verify:command"]["failedToolCalls"] == 1
    assert summary["totals"]["toolCalls"] == 4
    assert summary["totals"]["llmCalls"] == 1
    assert "nodes" not in summary  # counts-only payload


def test_counts_span_nodes_dropped_by_the_trim_limit():
    initialize()
    from backend import state

    _seed_card("T-TRIM")
    for i in range(6):
        state.TOOL_EXECUTION_LOG.append(
            _tool_event(f"ev{i}", f"2026-01-01 10:00:0{i}", "read_file", "T-TRIM")
        )
    with patch("backend.services.task_flow.list_step_traces_for_task", return_value=[]):
        flow = build_task_flow("T-TRIM", limit=2, include_full=False)

    entry = flow["workItemIndex"]["read:files"]
    assert flow["count"] == 2
    assert flow["totalCount"] == 6
    assert entry["toolCalls"] == 6  # counts are truthful beyond the trim
    assert len(entry["nodeIds"]) == 2  # links only point at rendered nodes


def test_duplicate_skip_is_logged_for_the_active_card_and_links_to_work_items():
    initialize()
    from backend import state
    from backend.services.tool_execution_service import log_duplicate_skip_event

    _seed_card("T-DUP")
    state.ACTIVE_SPRINT_TASK_ID = "T-DUP"
    try:
        log_duplicate_skip_event(
            agent="Developer",
            tool_name="run_command",
            arguments={"command": "flutter analyze"},
            tool_output="[skipped duplicate] Already ran 'run_command'",
            task_id=None,  # falls back to the active card, not "system"
        )
    finally:
        state.ACTIVE_SPRINT_TASK_ID = None

    logged = state.TOOL_EXECUTION_LOG[-1]
    assert logged["taskId"] == "T-DUP"
    assert logged["duplicateSkip"] is True
    assert logged["status"] == "skipped"

    with patch("backend.services.task_flow.list_step_traces_for_task", return_value=[]):
        flow = build_task_flow("T-DUP", limit=40, include_full=False)
    node = next(n for n in flow["nodes"] if n.get("toolName") == "run_command")
    assert node["duplicateSkip"] is True
    assert "verify:command" in (node.get("workItemIds") or [])
    assert flow["workItemIndex"]["verify:command"]["duplicateSkips"] == 1


def test_lane_advance_links_to_update_board_node():
    initialize()

    _seed_card("T-LANE")
    from backend import state

    state.TOOL_EXECUTION_LOG.append(
        _tool_event("ev-lane", "2026-01-01 10:00:00", "update_board", "T-LANE")
    )
    with patch("backend.services.task_flow.list_step_traces_for_task", return_value=[]):
        flow = build_task_flow("T-LANE", limit=40, include_full=False)
    node = next(n for n in flow["nodes"] if n.get("toolName") == "update_board")
    assert "lane:advance" in (node.get("workItemIds") or [])
    assert flow["workItemIndex"]["lane:advance"]["toolCalls"] == 1


def test_exit_reason_links_lane_and_blocked_items():
    initialize()
    from backend import state

    task = _seed_card("T-EXIT")
    items = derive_agent_work_items(task)
    ids = work_item_ids_for_node(items, {"kind": "llm", "exitReason": "duplicate_tool"})
    assert "lane:advance" in ids
    assert "blocked:tools" in ids or not any(i["id"] == "blocked:tools" for i in items)
    assert state.SHARED_BOARD["In Progress"][0]["id"] == "T-EXIT"


def test_board_state_items_are_marked_not_tool_linked():
    initialize()

    _seed_card("T-BOARD")
    with patch("backend.services.task_flow.list_step_traces_for_task", return_value=[]):
        summary = build_task_flow_summary("T-BOARD", limit=10)
    read = summary["workItemIndex"]["read:files"]
    assert read["toolLinked"] is True
    subtasks = summary["workItemIndex"].get("subtasks:children")
    if subtasks:
        assert subtasks["toolLinked"] is False


def test_move_to_done_restores_primary_model_and_clears_backup_counters():
    initialize()
    from backend import state
    from backend.agents.registry import agent_dev
    from backend.services.board_service import move_board_stage

    task = init_new_task({"id": "T-RST", "title": "Restore", "description": "d"})
    state.SHARED_BOARD = {"In Progress": [task], "Done": []}
    state.PRIMARY_MODELS = {**getattr(state, "PRIMARY_MODELS", {}), "dev": "primary-model:7b"}
    state.BACKUP_MODELS = {**getattr(state, "BACKUP_MODELS", {}), "dev": "backup-model:32b"}
    original_model = agent_dev.model
    agent_dev.model = "backup-model:32b"
    task["backupModelStepsRemaining"] = {"po": 0, "dev": 2, "cr": 0, "qa": 0}

    try:
        move_board_stage("T-RST", "Done")
        assert agent_dev.model == "primary-model:7b"
        assert task["backupModelStepsRemaining"]["dev"] == 0
    finally:
        agent_dev.model = original_model


def test_sprint_summary_restores_primary_model():
    initialize()
    from backend import state
    from backend.agents.registry import agent_dev
    from backend.services import sprint_service

    state.PRIMARY_MODELS = {**getattr(state, "PRIMARY_MODELS", {}), "dev": "primary-model:7b"}
    original_model = agent_dev.model
    agent_dev.model = "backup-model:32b"
    try:
        with patch.object(sprint_service, "save_sprint_summary"):
            with patch.object(sprint_service, "publish_event"):
                sprint_service._build_sprint_summary(3, "cancelled")
        assert agent_dev.model == "primary-model:7b"
    finally:
        agent_dev.model = original_model
