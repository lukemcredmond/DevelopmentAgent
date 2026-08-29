"""LLM debug entries are visible while requests run and complete in place."""

from backend import state
from backend.agents.scrum_agent import ScrumAgent
from backend.bootstrap import initialize
from backend.services.llm_debug_log import (
    append_llm_log_entry,
    complete_llm_log_entry,
    get_llm_logs,
)
from backend.services.llm_provider import ChatResult, ProviderMessage


def setup_function():
    initialize()
    with state.STATE_LOCK:
        state.LLM_DEBUG_LOG.clear()


def test_running_entry_completes_without_duplicate():
    entry = append_llm_log_entry(
        agent="Developer",
        agent_id="dev",
        task_id="T-LIVE",
        run_id="run-live",
        model="coder-model",
        iteration=1,
        request_messages=[{"role": "user", "content": "Implement the task"}],
        status="running",
    )

    visible = get_llm_logs(task_id="T-LIVE")
    assert len(visible) == 1
    assert visible[0]["status"] == "running"

    completed = complete_llm_log_entry(
        entry["id"],
        response_content="Done",
        response_tool_calls=[{"name": "write_file", "arguments": {"path": "safe.txt"}}],
        duration_ms=125,
        prompt_tokens=10,
        eval_tokens=3,
        total_tokens=13,
        tokens_reported=True,
    )

    assert completed is not None
    assert completed["status"] == "completed"
    assert completed["responseContent"] == "Done"
    assert len(get_llm_logs(task_id="T-LIVE")) == 1


def test_running_entry_can_complete_with_error():
    entry = append_llm_log_entry(
        agent="QA Tester",
        agent_id="qa",
        task_id="T-FAIL",
        model="qa-model",
        iteration=2,
        request_messages=[],
        status="running",
    )

    completed = complete_llm_log_entry(
        entry["id"],
        duration_ms=50,
        error="provider unavailable",
        error_type="connection",
    )

    assert completed is not None
    assert completed["status"] == "failed"
    assert completed["errorType"] == "connection"
    assert len(state.LLM_DEBUG_LOG) == 1


def test_sprint_chat_is_visible_before_provider_returns():
    class InspectingProvider:
        def chat(self, *args, **kwargs):
            visible = get_llm_logs(task_id="T-SPRINT")
            assert len(visible) == 1
            assert visible[0]["status"] == "running"
            return ChatResult(message=ProviderMessage(content="Sprint response"))

    agent = ScrumAgent(role="Developer", model="coder-model", system_prompt="system")
    result, error, _, _ = agent._single_chat_attempt(
        InspectingProvider(),
        [{"role": "user", "content": "Work the sprint card"}],
        stream=False,
        tools=None,
        iteration=1,
        task_id="T-SPRINT",
        agent_id="dev",
        run_id="run-sprint",
        tool_names=[],
    )

    assert result is not None
    assert error is None
    visible = get_llm_logs(task_id="T-SPRINT")
    assert len(visible) == 1
    assert visible[0]["status"] == "completed"
    assert visible[0]["responseContent"] == "Sprint response"
