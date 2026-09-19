"""Product Owner execute_step must load workflow settings before sampling."""

import json
from unittest.mock import patch

from backend import state
from backend.agents.registry import agent_po
from backend.bootstrap import initialize
from backend.services.po_clarification import PO_NUM_PREDICT_DEFAULT
from backend.services.sprint_service import PLANNING_OUTLINE_TASK_ID


class _FakeFunction:
    def __init__(self, name: str, arguments: dict):
        self.name = name
        self.arguments = json.dumps(arguments)


class _FakeToolCall:
    def __init__(self, name: str, arguments: dict):
        self.function = _FakeFunction(name, arguments)


class _FakeMessage:
    def __init__(self, content: str = "", tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls


class _FakeResponse:
    def __init__(self, content: str = "", tool_calls=None):
        self.message = _FakeMessage(content, tool_calls=tool_calls)


def test_po_execute_step_sets_num_predict_from_sampling():
    initialize()
    state.ACTIVE_SPRINT_TASK_ID = None
    state.ACTIVE_SPRINT_AGENT = None
    agent_po._step_num_predict = None
    seen: dict[str, int | None] = {}

    def fake_chat(*args, **kwargs):
        seen["num_predict"] = agent_po._step_num_predict
        return _FakeResponse("clarified the card")

    with patch(
        "backend.agents.scrum_agent.get_workflow_settings",
        return_value={
            "maxToolFailuresPerStep": 5,
            "maxAgentStepDurationSec": 30,
            "maxToolCallsPerStep": 80,
            "samplingByRole": {"po": {"num_predict": 4096}},
        },
    ), patch.object(agent_po, "_chat", side_effect=fake_chat), patch(
        "backend.agents.registry.configure_agent_tools"
    ), patch(
        "backend.storage.memory_engine.resolve_embed_model", return_value="embed"
    ), patch.object(
        agent_po, "_build_system_content", return_value="sys"
    ), patch.object(
        agent_po, "_build_user_content", return_value="user"
    ):
        result = agent_po.execute_step("clarify", max_iterations=1)

    assert seen.get("num_predict") == 4096
    assert agent_po._step_num_predict == 4096
    assert result


def test_po_execute_step_default_num_predict_without_override():
    initialize()
    state.ACTIVE_SPRINT_TASK_ID = None
    state.ACTIVE_SPRINT_AGENT = None
    agent_po._step_num_predict = None

    def fake_chat(*args, **kwargs):
        return _FakeResponse("clarified the card")

    with patch(
        "backend.agents.scrum_agent.get_workflow_settings",
        return_value={
            "maxToolFailuresPerStep": 5,
            "maxAgentStepDurationSec": 30,
            "maxToolCallsPerStep": 80,
        },
    ), patch.object(agent_po, "_chat", side_effect=fake_chat), patch(
        "backend.agents.registry.configure_agent_tools"
    ), patch(
        "backend.storage.memory_engine.resolve_embed_model", return_value="embed"
    ), patch.object(
        agent_po, "_build_system_content", return_value="sys"
    ), patch.object(
        agent_po, "_build_user_content", return_value="user"
    ):
        agent_po.execute_step("clarify", max_iterations=1)

    assert agent_po._step_num_predict == PO_NUM_PREDICT_DEFAULT


def test_po_planning_execute_step_keeps_explore_tools():
    initialize()
    state.ACTIVE_SPRINT_TASK_ID = PLANNING_OUTLINE_TASK_ID
    state.ACTIVE_SPRINT_AGENT = "Product Owner"
    seen: dict[str, object] = {}
    explore = [{"type": "function", "function": {"name": "list_dir"}}]

    def fake_chat(*args, **kwargs):
        seen["tools"] = kwargs.get("tools")
        return _FakeResponse("## Summary\nPlan\n\n## Proposed epics\n- Epic A")

    try:
        with patch(
            "backend.agents.scrum_agent.get_workflow_settings",
            return_value={
                "maxToolFailuresPerStep": 5,
                "maxAgentStepDurationSec": 30,
                "maxToolCallsPerStep": 80,
            },
        ), patch.object(agent_po, "_chat", side_effect=fake_chat), patch(
            "backend.agents.registry.configure_agent_tools"
        ), patch(
            "backend.storage.memory_engine.resolve_embed_model", return_value="embed"
        ), patch.object(
            agent_po, "_build_system_content", return_value="sys"
        ), patch.object(
            agent_po, "_build_user_content", return_value="user"
        ), patch.object(
            agent_po.registry,
            "get_ollama_tools",
            return_value=explore + [{"type": "function", "function": {"name": "add_backlog_tasks"}}],
        ):
            result = agent_po.execute_step("Produce a markdown plan", max_iterations=1)
    finally:
        state.ACTIVE_SPRINT_TASK_ID = None
        state.ACTIVE_SPRINT_AGENT = None

    names = [
        (t.get("function") or {}).get("name")
        for t in (seen.get("tools") or [])
        if isinstance(t, dict)
    ]
    assert names == ["list_dir"]
    assert "Summary" in (result or "")
    assert not str(result).startswith("Max tool iterations")


def test_po_planning_explore_then_markdown_plan():
    initialize()
    state.ACTIVE_SPRINT_TASK_ID = PLANNING_OUTLINE_TASK_ID
    state.ACTIVE_SPRINT_AGENT = "Product Owner"
    explore = [{"type": "function", "function": {"name": "list_dir"}}]
    chat_calls = {"count": 0}
    plan = "## Summary\nMeal planner.\n\n## Approach\nCore modules first.\n\n## Proposed epics\n- Recipes\n"

    def fake_chat(*args, **kwargs):
        chat_calls["count"] += 1
        if chat_calls["count"] == 1:
            return _FakeResponse(tool_calls=[_FakeToolCall("list_dir", {"path": "."})])
        return _FakeResponse(plan)

    def fake_exec(agent_id, tool_name, arguments, **kwargs):
        return type(
            "R",
            (),
            {
                "tool_name": tool_name,
                "tool_output": "backend/\nfrontend/\n",
                "success": True,
                "pending_approval": False,
            },
        )()

    try:
        with patch(
            "backend.agents.scrum_agent.get_workflow_settings",
            return_value={
                "maxToolFailuresPerStep": 5,
                "maxAgentStepDurationSec": 30,
                "maxToolCallsPerStep": 80,
            },
        ), patch.object(agent_po, "_chat", side_effect=fake_chat), patch(
            "backend.agents.scrum_agent.execute_tool", side_effect=fake_exec
        ), patch("backend.services.llm_context.prune_messages_if_needed", lambda m: m), patch(
            "backend.agents.registry.configure_agent_tools"
        ), patch(
            "backend.storage.memory_engine.resolve_embed_model", return_value="embed"
        ), patch.object(
            agent_po, "_build_system_content", return_value="sys"
        ), patch.object(
            agent_po, "_build_user_content", return_value="user"
        ), patch.object(
            agent_po.registry,
            "get_ollama_tools",
            return_value=explore,
        ):
            result = agent_po.execute_step("Produce a markdown plan", max_iterations=4)
    finally:
        state.ACTIVE_SPRINT_TASK_ID = None
        state.ACTIVE_SPRINT_AGENT = None

    assert chat_calls["count"] >= 2
    assert "Summary" in (result or "")
    assert not str(result).startswith("Max tool iterations")
