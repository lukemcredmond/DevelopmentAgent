"""write_file overwrite allowed on patch-escalation recovery paths."""

import json
import os
from unittest.mock import MagicMock, patch

from backend import state
from backend.agents.scrum_agent import ScrumAgent
from backend.bootstrap import initialize
from backend.services.patch_recovery import (
    build_write_file_escalation_nudge,
    compute_write_overwrite_allowed_paths,
)
from backend.services.tool_execution_service import ToolExecutionResult


def test_compute_write_overwrite_includes_escalated_and_force_patch():
    task = {"forcePatchNextDevStep": True, "files": [{"path": "lib/target.dart"}]}
    with patch(
        "backend.services.file_blocker.resolve_dev_edit_target_path",
        return_value="lib/target.dart",
    ):
        allowed = compute_write_overwrite_allowed_paths(
            {"lib/pending.dart"},
            {"lib/esc.dart"},
            task,
        )
    assert "lib/pending.dart" in allowed
    assert "lib/esc.dart" in allowed
    assert "lib/target.dart" in allowed


def test_escalation_nudge_promises_overwrite():
    msg = build_write_file_escalation_nudge(["lib/a.dart"], overwrite_allowed=True)
    assert "overwrite IS allowed" in msg
    assert "lib/a.dart" in msg


def test_write_file_allowed_on_existing_when_path_escalated(tmp_path):
    initialize()
    state.WORKSPACE_DIR = str(tmp_path)
    existing = tmp_path / "lib"
    existing.mkdir()
    target = existing / "foo.dart"
    target.write_text("old content", encoding="utf-8")

    agent = ScrumAgent.__new__(ScrumAgent)
    agent.role = "Developer"
    agent._publish_work_progress = MagicMock()
    agent._log_step_exit = MagicMock()
    agent._patch_recovery_paths = {"lib/foo.dart"}
    agent._patch_write_escalated_paths = {"lib/foo.dart"}
    agent._patch_write_overwrite_paths = {"lib/foo.dart"}

    call = MagicMock()
    call.function.name = "write_file"
    call.function.arguments = {"path": "lib/foo.dart", "content": "new content"}

    fake_result = ToolExecutionResult(
        tool_name="write_file",
        arguments=call.function.arguments,
        safe_args=call.function.arguments,
        tool_output="ok",
        success=True,
        duration_ms=1,
        timestamp="",
        agent="Developer",
        agent_id="dev",
        task_id="T-OVR",
        source="agent",
        run_id="r1",
    )

    with patch("backend.agents.scrum_agent.execute_tool", return_value=fake_result) as exec_mock, patch(
        "backend.agents.scrum_agent.finish_run"
    ), patch("backend.agents.scrum_agent.add_system_log"), patch(
        "backend.agents.scrum_agent.update_run"
    ), patch(
        "backend.agents.scrum_agent.find_task_by_id", return_value={"id": "T-OVR"}
    ):
        _name, _args, result, early = ScrumAgent._execute_single_tool_call(
            agent,
            call,
            task_id="T-OVR",
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


def test_write_file_blocked_without_escalation(tmp_path):
    initialize()
    state.WORKSPACE_DIR = str(tmp_path)
    lib = tmp_path / "lib"
    lib.mkdir()
    (lib / "foo.dart").write_text("x", encoding="utf-8")

    agent = ScrumAgent.__new__(ScrumAgent)
    agent.role = "Developer"
    agent._publish_work_progress = MagicMock()
    agent._log_step_exit = MagicMock()
    agent._patch_write_overwrite_paths = set()

    call = MagicMock()
    call.function.name = "write_file"
    call.function.arguments = {"path": "lib/foo.dart", "content": "y"}

    with patch("backend.agents.scrum_agent.execute_tool") as exec_mock, patch(
        "backend.agents.scrum_agent.finish_run"
    ), patch("backend.agents.scrum_agent.add_system_log"), patch(
        "backend.agents.scrum_agent.find_task_by_id", return_value={"id": "T-BLK"}
    ):
        _name, _args, result, early = ScrumAgent._execute_single_tool_call(
            agent,
            call,
            task_id="T-BLK",
            agent_id="dev",
            run_id="r1",
            user_prompt="go",
            failed_tool_keys=[],
            successful_tool_keys=[],
            total_failures=[0],
            max_tool_failures=5,
        )

    exec_mock.assert_not_called()
    assert result.success is False
    assert "write_file blocked" in result.tool_output
