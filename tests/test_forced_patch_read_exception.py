"""read_file on recovery target paths under forced patch before write_succeeded."""

import json
from unittest.mock import MagicMock, patch

from backend.agents.scrum_agent import ScrumAgent
from backend.services.dev_phase_graph import DevPhaseGraph
from backend.services.tool_execution_service import ToolExecutionResult


def test_recovery_read_path_allowed_for_pending_patch():
    agent = ScrumAgent.__new__(ScrumAgent)
    agent._patch_recovery_paths = {"lib/recover.dart"}
    assert agent._recovery_read_path_allowed(
        "read_file",
        {"path": "lib/recover.dart"},
        task_id="T1",
    )


def test_forced_patch_allows_read_on_edit_target():
    agent = ScrumAgent.__new__(ScrumAgent)
    agent._patch_recovery_paths = set()
    graph = DevPhaseGraph.for_new_step(force_patch=True)
    agent._dev_phase_graph = graph

    task = {"id": "T2", "files": [{"path": "lib/edit.dart"}]}
    with patch(
        "backend.services.file_blocker.resolve_dev_edit_target_path",
        return_value="lib/edit.dart",
    ), patch("backend.agents.scrum_agent.find_task_by_id", return_value=task):
        assert agent._recovery_read_path_allowed(
            "read_file",
            {"path": "lib/edit.dart"},
            task_id="T2",
        )


def test_forced_patch_read_executes_not_blocked():
    agent = ScrumAgent.__new__(ScrumAgent)
    agent.role = "Developer"
    agent._publish_work_progress = MagicMock()
    agent._log_step_exit = MagicMock()
    agent._patch_recovery_paths = {"lib/main.dart"}
    agent._dev_phase_graph = DevPhaseGraph.for_new_step(force_patch=True)

    call = MagicMock()
    call.function.name = "read_file"
    call.function.arguments = {"path": "lib/main.dart"}

    fake_result = ToolExecutionResult(
        tool_name="read_file",
        arguments=call.function.arguments,
        safe_args=call.function.arguments,
        tool_output="content",
        success=True,
        duration_ms=1,
        timestamp="",
        agent="Developer",
        agent_id="dev",
        task_id="T3",
        source="agent",
        run_id="r1",
    )

    with patch("backend.agents.scrum_agent.execute_tool", return_value=fake_result) as exec_mock, patch(
        "backend.agents.scrum_agent.finish_run"
    ), patch("backend.agents.scrum_agent.add_system_log"), patch(
        "backend.agents.scrum_agent.update_run"
    ), patch("backend.agents.scrum_agent.find_task_by_id", return_value={"id": "T3"}):
        _name, _args, result, early = ScrumAgent._execute_single_tool_call(
            agent,
            call,
            task_id="T3",
            agent_id="dev",
            run_id="r1",
            user_prompt="go",
            failed_tool_keys=[],
            successful_tool_keys=[],
            total_failures=[0],
            max_tool_failures=5,
        )

    exec_mock.assert_called_once()
    assert early is None
    assert result.success is True
