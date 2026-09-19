"""PO split fallbacks: JSON-only mode, deterministic stub, fail-fast tool JSON."""

from __future__ import annotations

import json
from unittest.mock import patch

from backend import state
from backend.agents.scrum_agent import ScrumAgent, po_execute_step_tools
from backend.agents.task_context import get_task_lane, init_new_task
from backend.bootstrap import initialize
from backend.services.sprint_service import (
    PO_SPLIT_MAX_ITERATIONS,
    PO_SPLIT_MAX_STEP_DURATION_SEC,
    run_po_split_task,
)
from backend.services.workflow_settings import reset_workflow_settings, save_workflow_settings


def _board_with(task):
    state.SHARED_BOARD = {
        "Backlog": [],
        "In Progress": [task],
        "Needs PO": [],
        "Needs User": [],
        "QA": [],
        "Done": [],
        "Features": [],
        "Refinement": [],
        "Code Review": [],
        "Blocked": [],
    }


def test_po_execute_step_tools_json_only_split():
    registry_tools = [
        {"type": "function", "function": {"name": "add_backlog_tasks"}},
        {"type": "function", "function": {"name": "read_file"}},
    ]
    tools, json_only = po_execute_step_tools(
        registry_tools,
        role="Product Owner",
        task_id="T-SPLIT",
        json_only_split=True,
    )
    assert json_only is True
    assert tools == []


def test_run_po_split_uses_json_only_mode_and_caps():
    initialize()
    reset_workflow_settings()
    task = init_new_task(
        {
            "id": "T-JSON-SPLIT",
            "title": "Big card",
            "description": "Needs splitting",
            "status": "In Progress",
        }
    )
    _board_with(task)
    json_response = json.dumps(
        [
            {
                "title": "Slice A",
                "description": "First part",
                "acceptanceCriteria": ["a1", "a2"],
            },
            {
                "title": "Slice B",
                "description": "Second part",
                "acceptanceCriteria": ["b1", "b2"],
            },
        ]
    )

    with patch("backend.services.sprint_service.agent_po.execute_step") as execute:
        execute.return_value = json_response
        result = run_po_split_task("T-JSON-SPLIT", "http://localhost:11434")

    execute.assert_called_once()
    kwargs = execute.call_args.kwargs
    assert kwargs.get("json_only_split") is True
    assert kwargs.get("max_iterations") == PO_SPLIT_MAX_ITERATIONS
    assert kwargs.get("max_step_duration_sec") == PO_SPLIT_MAX_STEP_DURATION_SEC
    assert result["added"] == 2
    assert result["parentDone"] is True
    assert get_task_lane("T-JSON-SPLIT") == "Done"
    assert len(state.SHARED_BOARD["Backlog"]) == 2


def test_run_po_split_deterministic_fallback_on_llm_failure():
    initialize()
    reset_workflow_settings()
    task = init_new_task(
        {
            "id": "T-1521",
            "title": "Big card",
            "description": "Do many things",
            "acceptanceCriteria": ["First slice", "Second slice"],
            "status": "In Progress",
        }
    )
    _board_with(task)

    with patch("backend.services.sprint_service.agent_po.execute_step") as execute:
        execute.return_value = (
            'LLM_CALL_FAILED: invalid tool call arguments for "add_backlog_tasks": '
            "unexpected end of JSON input"
        )
        result = run_po_split_task("T-1521", "http://localhost:11434")

    assert result["added"] >= 2
    assert result["parentDone"] is True
    assert get_task_lane("T-1521") == "Done"
    assert len(state.SHARED_BOARD["Backlog"]) >= 2
    parent = next(t for t in state.SHARED_BOARD["Done"] if t["id"] == "T-1521")
    assert parent.get("splitSuperseded") is True


def test_unrecoverable_invalid_tool_json_fails_fast():
    initialize()
    reset_workflow_settings()
    save_workflow_settings(
        {
            "ollamaMaxRetries": 4,
            "ollamaRetryDelaySec": [0, 0, 0, 0],
            "ollamaCooldownRetryEnabled": True,
            "ollamaCooldownRetrySec": 0,
            "ollamaCooldownRetryAttempts": 2,
        }
    )
    agent = ScrumAgent("Product Owner", "test-model", "system", "http://localhost:11434")
    err = (
        'invalid tool call arguments for "add_backlog_tasks": unexpected end of JSON input'
    )
    attempts = []

    def fake_single(*args, **kwargs):
        attempts.append(1)
        return None, err, "error", 100

    with patch.object(agent, "_single_chat_attempt", side_effect=fake_single):
        with patch("backend.agents.scrum_agent.time.sleep"):
            result = agent._chat(
                [{"role": "user", "content": "split"}],
                tools=[{"type": "function", "function": {"name": "add_backlog_tasks"}}],
            )

    assert result is None
    assert len(attempts) == 1
    assert agent._last_chat_error_type == "invalid_tool_json"
