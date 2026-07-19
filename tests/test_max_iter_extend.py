"""Max-iter stepProgress + extend continuation prompt."""

from unittest.mock import patch

from backend import state
from backend.agents.registry import agent_dev
from backend.agents.task_context import init_new_task, set_active_sprint_context
from backend.bootstrap import initialize
from backend.services.prompt_retry import build_continuation_prompt, extend_agent_step
from backend.services.step_diagnostics import build_step_progress, store_step_progress


def _empty_board():
    state.SHARED_BOARD.clear()
    for lane in (
        "Backlog",
        "In Progress",
        "Needs User",
        "Needs PO",
        "QA",
        "Done",
        "Refinement",
        "Code Review",
    ):
        state.SHARED_BOARD[lane] = []


class _FakeFunction:
    def __init__(self, name: str, arguments: dict):
        import json

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


def test_max_iterations_attaches_step_progress():
    initialize()
    _empty_board()
    task = init_new_task(
        {"id": "T-MAX", "title": "Max iter", "description": "d", "status": "In Progress"}
    )
    state.SHARED_BOARD["In Progress"] = [task]
    state.LAST_STEP_PROGRESS = None
    state.VIRTUAL_FILESYSTEM["lib/a.dart"] = "void a() {}\n"

    chat_calls = {"n": 0}

    def fake_chat(messages, **kwargs):
        chat_calls["n"] += 1
        # Always return text-only so _dev_step_needs_more_tools burns iterations
        return _FakeResponse(
            _FakeMessage(
                content="Here is my plan:\n1. Edit the file\n2. Done",
            )
        )

    def fake_exec(*args, **kwargs):
        return type(
            "R",
            (),
            {
                "tool_name": "read_file",
                "tool_output": "ok",
                "success": True,
                "pending_approval": False,
            },
        )()

    set_active_sprint_context("T-MAX", "Developer")
    with patch.object(agent_dev, "_chat", side_effect=fake_chat):
        with patch("backend.agents.scrum_agent.execute_tool", side_effect=fake_exec):
            with patch("backend.services.llm_context.prune_messages_if_needed", lambda m: m):
                # Force needs_more_tools path: In Progress + Developer
                result = agent_dev.execute_step("Implement", max_iterations=2)

    assert result.startswith("Max tool iterations")
    assert state.LAST_STEP_PROGRESS is not None
    assert state.LAST_STEP_PROGRESS["iterationsUsed"] == 2
    assert state.LAST_STEP_PROGRESS["iterationsMax"] == 2
    assert state.LAST_STEP_PROGRESS["taskId"] == "T-MAX"


def test_build_continuation_prompt_includes_tools():
    initialize()
    _empty_board()
    task = init_new_task({"id": "T-CONT", "title": "Continue", "description": "desc"})
    task["files"] = [{"path": "lib/main.dart", "action": "written"}]
    progress = {
        "taskId": "T-CONT",
        "iterationsUsed": 8,
        "iterationsMax": 8,
        "toolsUsed": ["read_file", "apply_patch"],
        "planRejections": 2,
        "textRejections": 1,
        "lastTools": [
            {"toolName": "apply_patch", "success": False, "summary": "old_text not found"},
        ],
        "lastToolSummary": "apply_patch: old_text not found",
        "stuckLoop": False,
    }
    prompt = build_continuation_prompt(task, "brief", progress)
    assert "CONTINUATION" in prompt
    assert "read_file" in prompt
    assert "apply_patch" in prompt
    assert "lib/main.dart" in prompt
    assert "Do not redo completed work" in prompt


def test_extend_reset_uses_retry_path():
    initialize()
    _empty_board()
    task = init_new_task({"id": "T-EXT", "title": "Extend", "description": "d"})
    state.SHARED_BOARD["In Progress"] = [task]
    store_step_progress(
        build_step_progress(
            task_id="T-EXT",
            iterations_used=8,
            iterations_max=8,
            tools_used={"read_file"},
        )
    )

    with patch(
        "backend.services.prompt_retry.retry_agent_step",
        return_value={"ok": True, "mode": "same", "output": "done"},
    ) as mock_retry:
        result = extend_agent_step(
            "T-EXT",
            "dev",
            "http://localhost:11434",
            action="reset",
        )
    assert result["action"] == "reset"
    mock_retry.assert_called_once()


def test_extend_builds_continuation_and_runs_step():
    initialize()
    _empty_board()
    task = init_new_task({"id": "T-EXT2", "title": "Extend2", "description": "d"})
    state.SHARED_BOARD["In Progress"] = [task]
    store_step_progress(
        {
            "taskId": "T-EXT2",
            "iterationsUsed": 8,
            "iterationsMax": 8,
            "toolsUsed": ["read_file", "grep"],
            "planRejections": 0,
            "textRejections": 0,
            "lastToolSummary": "grep: ok",
            "stuckLoop": False,
            "lastTools": [],
        }
    )

    captured = {}

    def fake_step(prompt, max_iterations=8):
        captured["prompt"] = prompt
        captured["max_iterations"] = max_iterations
        return "Patch applied."

    with patch.object(agent_dev, "execute_step", side_effect=fake_step):
        with patch(
            "backend.services.sprint_service._ensure_dev_step_trace",
            lambda *a, **k: None,
        ):
            with patch(
                "backend.services.sprint_service._record_last_step_outcome",
                lambda *a, **k: None,
            ):
                with patch(
                    "backend.services.step_diagnostics.finalize_active_step_trace",
                    lambda **k: None,
                ):
                    result = extend_agent_step(
                        "T-EXT2",
                        "dev",
                        "http://localhost:11434",
                        action="extend",
                        extra_iterations=4,
                    )

    assert result["ok"] is True
    assert result["extraIterations"] == 4
    assert captured["max_iterations"] == 4
    assert "CONTINUATION" in captured["prompt"]
    assert "grep" in captured["prompt"]
