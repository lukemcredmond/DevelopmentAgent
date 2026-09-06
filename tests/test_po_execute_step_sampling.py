"""Product Owner execute_step must load workflow settings before sampling."""

from unittest.mock import patch

from backend import state
from backend.agents.registry import agent_po
from backend.bootstrap import initialize
from backend.services.po_clarification import PO_NUM_PREDICT_DEFAULT


class _FakeMessage:
    def __init__(self, content: str):
        self.content = content
        self.tool_calls = None


class _FakeResponse:
    def __init__(self, content: str):
        self.message = _FakeMessage(content)


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
