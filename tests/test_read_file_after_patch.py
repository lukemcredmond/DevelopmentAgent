"""read_file duplicate skip after apply_patch must run real read on disk."""

import json
from unittest.mock import MagicMock, patch

from backend.agents.scrum_agent import ScrumAgent
from backend.services.tool_execution_service import ToolExecutionResult
from backend import state
from backend.workspace.files import (
    invalidate_step_file_read,
    read_file_in_step_duplicate_skip_allowed,
    record_step_file_read,
)
from backend.services.duplicate_tool_policy import purge_read_file_success_keys_for_path


def test_read_file_dup_skip_allowed_when_path_in_step_reads():
    state.STEP_FILE_READS.clear()
    record_step_file_read("lib/a.dart", "void main() {}")
    assert read_file_in_step_duplicate_skip_allowed({"path": "lib/a.dart"}) is True


def test_read_file_dup_skip_not_allowed_after_invalidate():
    state.STEP_FILE_READS.clear()
    record_step_file_read("lib/a.dart", "void main() {}")
    invalidate_step_file_read("lib/a.dart")
    assert read_file_in_step_duplicate_skip_allowed({"path": "lib/a.dart"}) is False


def test_purge_read_file_success_keys_for_path():
    args = {"path": "lib/a.dart"}
    key = ("read_file", json.dumps(args, sort_keys=True, default=str))
    other = ("grep", json.dumps({"pattern": "x"}, sort_keys=True))
    keys = [key, other]
    purge_read_file_success_keys_for_path(keys, "lib/a.dart")
    assert keys == [other]


def test_read_read_still_soft_skips_when_step_read_valid():
    agent = ScrumAgent.__new__(ScrumAgent)
    agent.role = "Developer"
    agent._publish_work_progress = MagicMock()
    agent._log_step_exit = MagicMock()

    call = MagicMock()
    call.function.name = "read_file"
    call.function.arguments = {"path": "pubspec.yaml"}

    state.STEP_FILE_READS.clear()
    record_step_file_read("pubspec.yaml", "name: app\n")

    args = {"path": "pubspec.yaml"}
    key = ("read_file", json.dumps(args, sort_keys=True, default=str))
    successful = [key]

    with patch("backend.agents.scrum_agent.finish_run"), patch(
        "backend.agents.scrum_agent.add_system_log"
    ), patch("backend.agents.scrum_agent._log_duplicate_skip"):
        _name, _out_args, result, early = ScrumAgent._execute_single_tool_call(
            agent,
            call,
            task_id=None,
            agent_id="dev",
            run_id="r1",
            user_prompt="go",
            failed_tool_keys=[],
            successful_tool_keys=successful,
            total_failures=[0],
            max_tool_failures=5,
        )
    assert early is None
    assert getattr(result, "duplicate_skip", False) is True


def test_read_after_patch_runs_execute_tool():
    agent = ScrumAgent.__new__(ScrumAgent)
    agent.role = "Developer"
    agent._publish_work_progress = MagicMock()
    agent._log_step_exit = MagicMock()

    call = MagicMock()
    call.function.name = "read_file"
    call.function.arguments = {"path": "lib/main.dart"}

    state.STEP_FILE_READS.clear()
    record_step_file_read("lib/main.dart", "before patch")
    invalidate_step_file_read("lib/main.dart")

    args = {"path": "lib/main.dart"}
    key = ("read_file", json.dumps(args, sort_keys=True, default=str))
    successful = [key]

    fake_result = ToolExecutionResult(
        tool_name="read_file",
        arguments=args,
        safe_args=args,
        tool_output="after patch content",
        success=True,
        duration_ms=1,
        timestamp="",
        agent="Developer",
        agent_id="dev",
        task_id="T1",
        source="agent",
        run_id="r1",
    )

    with patch("backend.agents.scrum_agent.execute_tool", return_value=fake_result) as exec_mock, patch(
        "backend.agents.scrum_agent.finish_run"
    ), patch("backend.agents.scrum_agent.add_system_log"), patch(
        "backend.agents.scrum_agent.update_run"
    ):
        _name, _out_args, result, early = ScrumAgent._execute_single_tool_call(
            agent,
            call,
            task_id="T1",
            agent_id="dev",
            run_id="r1",
            user_prompt="go",
            failed_tool_keys=[],
            successful_tool_keys=successful,
            total_failures=[0],
            max_tool_failures=5,
        )

    exec_mock.assert_called_once()
    assert early is None
    assert getattr(result, "duplicate_skip", False) is False
    assert result.tool_output == "after patch content"


def test_successful_apply_patch_purges_read_dup_keys():
    agent = ScrumAgent.__new__(ScrumAgent)
    agent.role = "Developer"
    agent._publish_work_progress = MagicMock()
    agent._log_step_exit = MagicMock()

    call = MagicMock()
    call.function.name = "apply_patch"
    call.function.arguments = {"path": "lib/x.dart", "old_text": "a", "new_text": "b"}

    read_key = (
        "read_file",
        json.dumps({"path": "lib/x.dart"}, sort_keys=True, default=str),
    )
    successful = [read_key]

    fake_result = ToolExecutionResult(
        tool_name="apply_patch",
        arguments=call.function.arguments,
        safe_args=call.function.arguments,
        tool_output="ok",
        success=True,
        duration_ms=1,
        timestamp="",
        agent="Developer",
        agent_id="dev",
        task_id="T1",
        source="agent",
        run_id="r1",
    )

    with patch("backend.agents.scrum_agent.execute_tool", return_value=fake_result), patch(
        "backend.agents.scrum_agent.finish_run"
    ), patch("backend.agents.scrum_agent.add_system_log"), patch(
        "backend.agents.scrum_agent.update_run"
    ):
        ScrumAgent._execute_single_tool_call(
            agent,
            call,
            task_id="T1",
            agent_id="dev",
            run_id="r1",
            user_prompt="go",
            failed_tool_keys=[],
            successful_tool_keys=successful,
            total_failures=[0],
            max_tool_failures=5,
        )

    assert read_key not in successful
