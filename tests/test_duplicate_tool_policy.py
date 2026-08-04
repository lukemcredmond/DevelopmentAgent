"""Duplicate tool policy and task working context."""

from __future__ import annotations

import json
from unittest.mock import patch

from backend.agents.registry import agent_dev
from backend.agents.scrum_agent import SAME_ARGS_SUCCESS_LIMIT, ScrumAgent
from backend.agents.task_context import build_task_prompt, init_new_task, normalize_task
from backend.agents.tool_fingerprints import finalize_step_tool_fingerprints, tool_fingerprint_key
from backend.bootstrap import initialize
from backend.services.duplicate_tool_policy import (
    duplicate_cross_step_block_applies,
    duplicate_in_step_hard_stop_applies,
    duplicate_in_step_soft_skip_applies,
)
from backend.services.task_working_context import (
    append_working_context,
    format_working_context_for_prompt,
    record_tool_working_context,
)


def test_run_command_strict_duplicate_policy_by_default():
    assert duplicate_in_step_hard_stop_applies("run_command") is True
    assert duplicate_in_step_soft_skip_applies("run_command") is True
    assert duplicate_in_step_hard_stop_applies("read_file") is True
    assert duplicate_in_step_soft_skip_applies("read_file") is True


def test_normalize_run_command_for_duplicate():
    from backend.services.duplicate_tool_policy import normalize_run_command_for_duplicate

    assert normalize_run_command_for_duplicate("  Flutter   Clean  ") == "flutter clean"
    assert normalize_run_command_for_duplicate("flutter clean") == "flutter clean"


def test_run_command_off_policy_allows_repeat_when_excluded():
    ws = {
        "duplicateToolPolicy": "strict",
        "duplicateRunCommandPolicy": "off",
        "duplicateToolHardStopExclude": ["run_command"],
    }
    assert duplicate_in_step_hard_stop_applies("run_command", ws) is False
    assert duplicate_in_step_soft_skip_applies("run_command", ws) is False


def test_readonly_tools_exempt_from_cross_step_block():
    assert duplicate_cross_step_block_applies("read_file") is False
    assert duplicate_cross_step_block_applies("list_dir") is False
    assert duplicate_cross_step_block_applies("grep") is False
    assert duplicate_cross_step_block_applies("glob_file_search") is False
    assert duplicate_cross_step_block_applies("apply_patch") is True


def test_run_command_cross_step_block_when_not_excluded():
    assert duplicate_cross_step_block_applies("run_command", stop_reason="duplicate_tool") is True
    assert duplicate_cross_step_block_applies("run_command", stop_reason="tool_failure_stop") is True
    ws = {
        "duplicateToolPolicy": "strict",
        "duplicateRunCommandPolicy": "off",
        "duplicateToolHardStopExclude": ["run_command"],
    }
    assert duplicate_cross_step_block_applies("run_command", stop_reason="duplicate_tool", ws=ws) is False


def test_finalize_blocks_run_command_on_duplicate_tool_by_default():
    initialize()
    task = init_new_task({"id": "T-DUP", "title": "x", "description": "d", "status": "In Progress"})
    key = tool_fingerprint_key("run_command", {"command": "flutter analyze"})
    finalize_step_tool_fingerprints(
        task,
        [key],
        stop_reason="duplicate_tool",
    )
    blocked = task.get("blockedToolFingerprints") or []
    assert any(e.get("tool") == "run_command" for e in blocked if isinstance(e, dict))


def test_working_context_in_build_task_prompt():
    initialize()
    task = init_new_task({"id": "T-WC", "title": "Card", "description": "d", "status": "In Progress"})
    append_working_context(task, kind="command", summary="flutter analyze exit 0")
    prompt = build_task_prompt(task, "brief", agent_role="Developer")
    assert "WORKING CONTEXT" in prompt
    assert "flutter analyze" in prompt


def test_record_tool_write_bumps_generation():
    task = init_new_task({"id": "T-G", "title": "x", "description": "d", "status": "In Progress"})
    record_tool_working_context(
        task,
        tool_name="apply_patch",
        arguments={"path": "a.dart"},
        tool_output="ok",
        success=True,
    )
    assert int(task.get("workspaceGeneration") or 0) >= 1
    block = format_working_context_for_prompt(task)
    assert "apply_patch" in block


class _FakeFunction:
    def __init__(self, name: str, arguments: dict):
        self.name = name
        self.arguments = json.dumps(arguments)


class _FakeToolCall:
    def __init__(self, name: str, arguments: dict):
        self.function = _FakeFunction(name, arguments)


def test_run_command_second_identical_call_soft_skips_when_replay_exists():
    """Strict run_command policy: second identical success replays cache when present."""
    initialize()
    agent = ScrumAgent(role="Developer", model="m", system_prompt="test")
    call = _FakeToolCall("run_command", {"command": "flutter clean"})
    from backend.services.duplicate_tool_policy import normalize_run_command_for_duplicate
    from backend.services.tool_cache import clear_tool_cache, store_cached_result

    clear_tool_cache()
    cmd = normalize_run_command_for_duplicate("flutter clean")
    dup_key = ("run_command", json.dumps({"command": cmd}, sort_keys=True))
    successful = [dup_key]
    store_cached_result("run_command", {"command": "flutter clean"}, "clean ok", True)
    exec_count = {"n": 0}

    def fake_execute(*_args, **_kwargs):
        exec_count["n"] += 1
        from backend.services.tool_execution_service import ToolExecutionResult

        return ToolExecutionResult(
            tool_name="run_command",
            arguments={"command": "flutter clean"},
            safe_args={},
            tool_output="ok",
            success=True,
            duration_ms=1,
            timestamp="",
            agent="Developer",
            agent_id="dev",
            task_id="T1",
            source="agent",
            run_id="R1",
        )

    with patch.object(agent, "_publish_work_progress"):
        with patch("backend.agents.scrum_agent.execute_tool", side_effect=fake_execute):
            with patch("backend.agents.scrum_agent.find_task_by_id", return_value=None):
                _tool_name, _args, result, early = agent._execute_single_tool_call(
                    call,
                    task_id=None,
                    agent_id="dev",
                    run_id="R1",
                    user_prompt="",
                    failed_tool_keys=[],
                    successful_tool_keys=successful,
                    total_failures=[0],
                    max_tool_failures=5,
                )
    assert early is None
    assert exec_count["n"] == 0
    assert getattr(result, "duplicate_skip", False) is True
    assert "clean ok" in result.tool_output
    assert "[duplicate tool" in result.tool_output


def test_run_command_second_identical_call_executes_without_replay():
    """When no cached/transcript output exists, duplicate policy allows a real run."""
    initialize()
    from backend.services.tool_cache import clear_tool_cache

    clear_tool_cache()
    agent = ScrumAgent(role="Developer", model="m", system_prompt="test")
    call = _FakeToolCall("run_command", {"command": "flutter clean"})
    from backend.services.duplicate_tool_policy import normalize_run_command_for_duplicate

    cmd = normalize_run_command_for_duplicate("flutter clean")
    dup_key = ("run_command", json.dumps({"command": cmd}, sort_keys=True))
    successful = [dup_key]
    exec_count = {"n": 0}

    def fake_execute(*_args, **_kwargs):
        exec_count["n"] += 1
        from backend.services.tool_execution_service import ToolExecutionResult

        return ToolExecutionResult(
            tool_name="run_command",
            arguments={"command": "flutter clean"},
            safe_args={},
            tool_output="ok",
            success=True,
            duration_ms=1,
            timestamp="",
            agent="Developer",
            agent_id="dev",
            task_id="T1",
            source="agent",
            run_id="R1",
        )

    with patch.object(agent, "_publish_work_progress"):
        with patch("backend.agents.scrum_agent.execute_tool", side_effect=fake_execute):
            with patch("backend.agents.scrum_agent.find_task_by_id", return_value=None):
                _tool_name, _args, result, early = agent._execute_single_tool_call(
                    call,
                    task_id=None,
                    agent_id="dev",
                    run_id="R1",
                    user_prompt="",
                    failed_tool_keys=[],
                    successful_tool_keys=successful,
                    total_failures=[0],
                    max_tool_failures=5,
                )
    assert early is None
    assert exec_count["n"] == 1
    assert result.success is True
    assert getattr(result, "duplicate_skip", False) is False


def test_grep_fourth_identical_call_hard_stops_loop():
    """After LIMIT identical successes, replay is disabled and the step stops."""
    initialize()
    from backend.services.tool_cache import clear_tool_cache, store_cached_result

    clear_tool_cache()
    agent = ScrumAgent(role="Developer", model="m", system_prompt="test")
    call = _FakeToolCall("grep", {"pattern": "Widget"})
    dup_key = ("grep", json.dumps({"pattern": "Widget"}, sort_keys=True))
    successful = [dup_key, dup_key, dup_key]
    store_cached_result(
        "grep",
        {"pattern": "Widget"},
        "Grep 'Widget' (1 match(es)):\n- a.dart:1: Widget",
        True,
    )
    exec_count = {"n": 0}

    def fake_execute(*_args, **_kwargs):
        exec_count["n"] += 1
        from backend.services.tool_execution_service import ToolExecutionResult

        return ToolExecutionResult(
            tool_name="grep",
            arguments={"pattern": "Widget"},
            safe_args={},
            tool_output="should not run",
            success=True,
            duration_ms=1,
            timestamp="",
            agent="Developer",
            agent_id="dev",
            task_id="T1",
            source="agent",
            run_id="R1",
        )

    with patch.object(agent, "_publish_work_progress"):
        with patch.object(agent, "_log_step_exit"):
            with patch("backend.agents.scrum_agent.finish_run"):
                with patch("backend.agents.scrum_agent.execute_tool", side_effect=fake_execute):
                    with patch("backend.agents.scrum_agent.find_task_by_id", return_value=None):
                        _tool_name, _args, result, early = agent._execute_single_tool_call(
                            call,
                            task_id="T1",
                            agent_id="dev",
                            run_id="R1",
                            user_prompt="",
                            failed_tool_keys=[],
                            successful_tool_keys=successful,
                            total_failures=[0],
                            max_tool_failures=5,
                        )
    assert exec_count["n"] == 0
    assert early is not None
    assert "loop detected" in early.lower()
    assert result.success is False


def test_duplicate_replay_includes_loop_breaker_header():
    from backend.services.duplicate_tool_policy import apply_duplicate_loop_breaker_to_output

    body = "Grep 'foo' (0 matches)"
    wrapped = apply_duplicate_loop_breaker_to_output(
        "grep",
        {"pattern": "foo"},
        body,
        identical_prior_successes=1,
        limit=SAME_ARGS_SUCCESS_LIMIT,
    )
    assert "[duplicate tool" in wrapped
    assert "do not" in wrapped.lower()
    assert body in wrapped
