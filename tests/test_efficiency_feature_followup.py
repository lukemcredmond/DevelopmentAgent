"""Efficiency defaults: excerpt inject, blocked fingerprint dispatch, feature follow-up."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from backend.agents.scrum_agent import ScrumAgent
from backend.agents.task_context import build_task_prompt, init_new_task
from backend.agents.tool_fingerprints import (
    block_tool_fingerprint_on_task,
    is_tool_fingerprint_blocked,
)
from backend.bootstrap import initialize
from backend.services.feature_service import create_feature, intake_feature_offline
from backend.services.sprint_service import run_po_add_feature
from backend.services.workflow_settings import DEFAULT_WORKFLOW_SETTINGS
from backend.workspace.files import build_sprint_file_context


def test_defaults_favor_latency():
    assert DEFAULT_WORKFLOW_SETTINGS.get("sprintFileContextMode") == "excerpt"
    assert DEFAULT_WORKFLOW_SETTINGS.get("autoExtendOnMaxIter") is False
    assert DEFAULT_WORKFLOW_SETTINGS.get("ollamaNumCtxAuto") is True
    assert DEFAULT_WORKFLOW_SETTINGS.get("semanticSprintTopK") == 3


def test_excerpt_mode_caps_large_file_bodies():
    initialize()
    from backend import state

    big = "line\n" * 200 + ("BODY" * 500)
    state.VIRTUAL_FILESYSTEM["big.py"] = big
    task = init_new_task(
        {
            "id": "T-EX",
            "title": "t",
            "description": "d",
            "files": [{"path": "big.py", "action": "written"}],
        }
    )
    excerpt, paths = build_sprint_file_context(task, max_chars=8000, mode="excerpt")
    assert "big.py" in paths
    assert "PRE-LOADED FILE CONTEXT" in excerpt
    assert "excerpts" in excerpt
    assert "BODY" * 50 not in excerpt  # full body not injected
    assert len(excerpt) < 2000

    full, _ = build_sprint_file_context(task, max_chars=50000, mode="full")
    assert "BODY" * 50 in full


def test_is_tool_fingerprint_blocked_matches_args():
    task = {"id": "T-FP"}
    block_tool_fingerprint_on_task(task, "run_command", {"command": "npm test"})
    assert is_tool_fingerprint_blocked(task, "run_command", {"command": "npm test"}) is True
    assert is_tool_fingerprint_blocked(task, "run_command", {"command": "npm run lint"}) is False


def test_blocked_fingerprint_skips_execute_tool():
    initialize()
    from backend import state

    task = init_new_task({"id": "T-BLK-DISP", "title": "t", "description": "d"})
    block_tool_fingerprint_on_task(task, "read_file", {"path": "a.ts"})
    state.SHARED_BOARD = {"In Progress": [task]}
    state.TOOL_EXECUTION_LOG.clear()
    state.ACTIVE_SPRINT_TASK_ID = "T-BLK-DISP"
    agent = ScrumAgent(role="Developer", model="test", system_prompt="x")
    call = SimpleNamespace(
        function=SimpleNamespace(name="read_file", arguments={"path": "a.ts"})
    )
    try:
        with patch("backend.services.tool_execution_service.execute_tool") as exec_tool:
            name, args, result, early = agent._execute_single_tool_call(
                call,
                task_id="T-BLK-DISP",
                agent_id="dev",
                run_id="run-1",
                user_prompt="do it",
                failed_tool_keys=[],
                successful_tool_keys=[],
                total_failures=[0],
                max_tool_failures=5,
            )
            assert exec_tool.call_count == 0
            assert name == "read_file"
            assert early is None
            assert result.success is False
            assert getattr(result, "duplicate_skip", False) is True
            assert "blocked fingerprint" in result.tool_output.lower()
            assert any(e.get("duplicateSkip") for e in state.TOOL_EXECUTION_LOG)
    finally:
        state.ACTIVE_SPRINT_TASK_ID = None


def test_observation_summary_folds_read_nudge_without_extra_system():
    agent = ScrumAgent(role="Developer", model="test", system_prompt="x")
    messages: list = []
    result = SimpleNamespace(
        success=True,
        tool_output="1| hello",
        duplicate_skip=False,
    )
    with patch(
        "backend.agents.scrum_agent.get_workflow_settings",
        return_value={"enableObservationSummaries": True, "maxInCardLintFixes": 5},
    ):
        agent._append_tool_messages(
            messages, "read_file", {"path": "a.ts"}, "1| hello", True
        )
        agent._append_observation_summary(
            messages, [("read_file", {"path": "a.ts"}, result)]
        )
    system_msgs = [m for m in messages if m.get("role") == "system"]
    tool_msgs = [m for m in messages if m.get("role") == "tool"]
    assert len(tool_msgs) == 1
    assert len(system_msgs) == 1
    assert "OBSERVATION" in system_msgs[0]["content"]
    assert "apply_patch" in system_msgs[0]["content"]


def test_cr_prompt_is_lighter_than_dev():
    initialize()
    task = init_new_task({"id": "T-SLIM", "title": "Slim", "description": "d"})
    task["decisions"] = [
        {
            "timestamp": f"t{i}",
            "agent": "Developer",
            "type": "note",
            "summary": f"decision {i}",
            "detail": "x" * 100,
        }
        for i in range(10)
    ]
    task["transcript"] = [
        {"timestamp": f"t{i}", "agent": "Developer", "content": f"msg {i}"}
        for i in range(8)
    ]
    from backend import state

    state.SHARED_BOARD = {"In Progress": [task]}
    slim = build_task_prompt(task, "brief", agent_role="Code Reviewer")
    full = build_task_prompt(task, "brief", agent_role="Developer")
    assert slim.count("decision ") <= full.count("decision ")
    assert "Tool reuse guidance" not in slim
    assert "Tool reuse guidance" in full


def test_intake_offline_updates_preferred_feature():
    initialize()
    from backend import state
    from backend.services.board_lanes import normalize_board_lanes

    state.SHARED_BOARD.clear()
    normalize_board_lanes(state.SHARED_BOARD)
    feature, first = create_feature(
        "Payments",
        "Stripe",
        request_title="Payments",
        request_body="Add Stripe",
        child_task={"title": "Setup", "description": "d", "acceptanceCriteria": ["ok"]},
    )
    fid = feature["id"]
    children_before = list(feature.get("childTaskIds") or [])
    updated, child = intake_feature_offline(
        "Add refunds",
        "Support refunds API",
        preferred_feature_id=fid,
    )
    assert updated["id"] == fid
    assert child["id"] not in children_before or child["featureId"] == fid
    assert child.get("featureId") == fid
    assert len(updated.get("childTaskIds") or []) >= len(children_before)


def test_run_po_add_feature_preferred_forces_update_path():
    initialize()
    from backend import state
    from backend.services.board_lanes import normalize_board_lanes

    state.SHARED_BOARD.clear()
    normalize_board_lanes(state.SHARED_BOARD)
    feature, _ = create_feature(
        "Auth",
        "Login",
        request_title="Auth",
        request_body="Login",
        child_task={"title": "Login form", "description": "d", "acceptanceCriteria": ["ok"]},
    )
    fid = feature["id"]

    po_json = (
        '{"action":"update","featureId":"'
        + fid
        + '","featureTitle":"Auth","featureDescription":"Login + logout",'
        '"historySummary":"Add logout","childTask":{"title":"Logout","description":"Add logout",'
        '"acceptanceCriteria":["user can log out"]}}'
    )
    with patch("backend.services.sprint_service.agent_po") as po:
        po.execute_step.return_value = po_json
        po.ollama_url = ""
        run_po_add_feature(
            "Add logout",
            "Logout button",
            "http://localhost:11434",
            preferred_feature_id=fid,
        )
    feat = next(t for t in state.SHARED_BOARD.get("Features", []) if t["id"] == fid)
    assert len(feat.get("childTaskIds") or []) >= 2
