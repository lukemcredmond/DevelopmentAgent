"""read_file result must reach the next LLM iteration before apply_patch."""

import json
from unittest.mock import patch

from backend import state
from backend.agents.registry import agent_dev
from backend.agents.task_context import init_new_task, set_active_sprint_context
from backend.bootstrap import initialize
from backend.services.sprint_service import _dev_step_read_only_no_edits, run_in_progress_step


class _FakeFunction:
    def __init__(self, name: str, arguments: dict):
        self.name = name
        self.arguments = json.dumps(arguments)


class _FakeToolCall:
    def __init__(self, name: str, arguments: dict):
        self.function = _FakeFunction(name, arguments)


class _FakeMessage:
    def __init__(self, *, content=None, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls


class _FakeResponse:
    def __init__(self, message):
        self.message = message


def test_read_file_output_in_messages_before_second_llm_call():
    initialize()
    state.SHARED_BOARD.clear()
    for lane in ("Backlog", "In Progress", "Needs User", "Needs PO", "QA", "Done", "Refinement", "Code Review"):
        state.SHARED_BOARD[lane] = []

    task = init_new_task({"id": "T-RF", "title": "Read then patch", "description": "d", "status": "In Progress"})
    state.SHARED_BOARD["In Progress"] = [task]
    state.VIRTUAL_FILESYSTEM["lib/main.dart"] = "void main() {}\n"

    captured_messages = []
    chat_calls = {"count": 0}

    def fake_chat(messages, **kwargs):
        chat_calls["count"] += 1
        captured_messages.append(list(messages))
        if chat_calls["count"] == 1:
            return _FakeResponse(
                _FakeMessage(tool_calls=[_FakeToolCall("read_file", {"path": "lib/main.dart"})])
            )
        if chat_calls["count"] == 2:
            tool_msgs = [m for m in messages if isinstance(m, dict) and m.get("role") == "tool"]
            assert tool_msgs, "iteration 2 must include read_file tool output"
            assert "void main()" in tool_msgs[0].get("content", "")
            return _FakeResponse(_FakeMessage(content="Done reviewing file."))
        return _FakeResponse(_FakeMessage(content="unexpected iteration"))

    set_active_sprint_context("T-RF", "Developer")
    state.STEP_FILE_READS.clear()

    def fake_exec(agent_id, tool_name, arguments, **kwargs):
        from backend.workspace.files import record_step_file_read

        output = "void main() {}\n"
        if tool_name == "read_file":
            record_step_file_read(str(arguments.get("path") or ""), output)
        return type(
            "R",
            (),
            {
                "tool_name": tool_name,
                "tool_output": output,
                "success": True,
                "pending_approval": False,
            },
        )()

    with patch.object(agent_dev, "_chat", side_effect=fake_chat):
        with patch("backend.agents.scrum_agent.execute_tool", side_effect=fake_exec):
            with patch("backend.services.llm_context.prune_messages_if_needed", lambda m: m):
                result = agent_dev.execute_step("Implement lib/main.dart", max_iterations=4)

    assert chat_calls["count"] >= 2
    assert "lib/main.dart" in state.STEP_FILE_READS
    assert result == "Done reviewing file."


def test_dev_step_read_only_no_edits_detected():
    initialize()
    task = init_new_task({"id": "T-RO", "title": "Read only", "description": "d", "status": "In Progress"})
    step_started = "2026-01-01 00:00:00"
    task["transcript"] = [
        {
            "role": "tool",
            "toolName": "read_file",
            "toolSuccess": True,
            "timestamp": "2026-01-01 00:00:01",
            "content": "read_file → lib/a.dart ✓",
        }
    ]
    state.SHARED_BOARD["In Progress"] = [task]

    assert _dev_step_read_only_no_edits(task, "In Progress", step_started) is True


def test_run_in_progress_read_only_sets_last_step_outcome():
    initialize()
    state.SHARED_BOARD.clear()
    for lane in ("Backlog", "In Progress", "Needs User", "Needs PO", "QA", "Done", "Refinement", "Code Review"):
        state.SHARED_BOARD[lane] = []

    task = init_new_task({"id": "T-IP", "title": "Active", "description": "d", "status": "In Progress"})
    state.SHARED_BOARD["In Progress"] = [task]

    def fake_dev_step(active_task, *_args, **_kwargs):
        state.LAST_AGENT_STEP_RESULT = "Reviewed file."
        state.DEV_STEP_READ_ONLY_NO_EDITS = True

    with patch("backend.services.sprint_service._run_developer_step", side_effect=fake_dev_step):
        run_in_progress_step("brief", "http://localhost:11434")

    assert state.LAST_STEP_OUTCOME is not None
    assert state.LAST_STEP_OUTCOME["ok"] is False
    assert "read files but made no edits" in state.LAST_STEP_OUTCOME["message"]
