"""Model timeline merge tests."""

from backend.bootstrap import initialize
from backend.services.llm_debug_log import append_llm_log_entry
from backend.services.model_timeline import build_model_timeline
from backend.services.tool_execution_service import append_global_tool_event


def test_timeline_merges_llm_and_tool():
    initialize()
    from backend import state

    state.LLM_DEBUG_LOG.clear()
    state.TOOL_EXECUTION_LOG.clear()

    append_llm_log_entry(
        agent="Developer",
        agent_id="dev",
        task_id="T-TIME",
        run_id="run-1",
        model="test",
        iteration=1,
        request_messages=[],
        response_content="I'll read the file",
        response_tool_calls=[{"name": "read_file", "arguments": {"path": "a.py"}}],
    )
    append_global_tool_event(
        {
            "eventId": "ev-1",
            "runId": "run-1",
            "taskId": "T-TIME",
            "agent": "Developer",
            "toolName": "read_file",
            "toolArgs": {"path": "a.py"},
            "toolSuccess": True,
            "toolOutput": "file contents",
            "durationMs": 12,
            "timestamp": "2026-01-01 12:00:01",
            "source": "agent",
            "status": "completed",
        }
    )
    append_llm_log_entry(
        agent="Developer",
        agent_id="dev",
        task_id="T-TIME",
        run_id="run-1",
        model="test",
        iteration=2,
        request_messages=[],
        response_content="Done patching",
    )

    result = build_model_timeline(task_id="T-TIME", limit=50)
    kinds = [item["kind"] for item in result["items"]]
    assert kinds.count("llm") == 2
    assert kinds.count("tool") == 1
    assert result["threads"][0]["taskId"] == "T-TIME"
