"""Regression tests for framework performance and correctness plan."""

from backend.agents.registry import _guarded_update_board
from backend.bootstrap import initialize
from backend.services.board_lanes import canonical_lane
from backend.services.dev_phase_graph import DevPhaseGraph
from backend.services.duplicate_tool_policy import (
    duplicate_cross_step_block_applies,
    duplicate_in_step_action,
    duplicate_in_step_hard_stop_applies,
    tools_log_has_verify,
)
from backend.services.sprint_service import (
    _qa_result_indicates_failure,
    qa_gate_blocks_done,
)


def test_canonical_lane_normalizes_done():
    assert canonical_lane("done") == "Done"
    assert canonical_lane("Done ") == "Done"
    assert canonical_lane("bogus") is None


def test_guards_unknown_lane_on_update_board():
    initialize()
    from backend import state
    from backend.agents.task_context import init_new_task

    state.SHARED_BOARD.setdefault("In Progress", [])
    task = init_new_task({"id": "T-LANE-TEST", "title": "T", "description": "d"})
    state.SHARED_BOARD["In Progress"].append(task)
    out = _guarded_update_board("T-LANE-TEST", "not_a_lane")
    assert "unknown lane" in out.lower()


def test_qa_pass_message_not_failure():
    assert not _qa_result_indicates_failure("QA PASS: all tests pass, 0 failures, nothing failed.")


def test_qa_structured_fail():
    assert _qa_result_indicates_failure("QA: FAIL — regression in auth")


def test_relaxed_done_gate_blocks_failed_playbook():
    task = {
        "id": "T1",
        "acceptanceCriteria": ["a"],
        "acChecklist": [True],
        "qaEvidence": {"playbookRun": True, "passed": False},
        "fixVerifyLintClean": True,
    }
    from backend.services.workflow_settings import reset_workflow_settings, save_workflow_settings

    reset_workflow_settings()
    save_workflow_settings(
        {
            "executionProfile": "implementer",
            "implementerRelaxGatesAlways": True,
            "implementerRequireAnalyzeClean": False,
            "implementerRequireAcChecklistForDone": False,
        }
    )
    blocked, _ = qa_gate_blocks_done(task)
    assert blocked is True


def test_forced_patch_allows_read_file():
    graph = DevPhaseGraph()
    graph.start_forced_patch(in_step=True)
    assert graph.should_block_explore_tool("read_file") is False
    assert graph.should_block_explore_tool("grep") is True


def test_duplicate_in_step_action_unified():
    assert duplicate_in_step_action("apply_patch") == "stop"
    assert duplicate_in_step_hard_stop_applies("apply_patch") is True


def test_cross_step_block_requires_failure_stop_reason():
    assert duplicate_cross_step_block_applies("apply_patch", stop_reason="completed_with_writes") is False
    assert duplicate_cross_step_block_applies("apply_patch", stop_reason="tool_failure_stop") is True


def test_tools_log_system_verify_after_write():
    log = [
        {"toolName": "apply_patch", "success": True},
        {"toolName": "system_verify", "success": True, "summary": "ok"},
    ]
    assert tools_log_has_verify(log) is True


def test_step_num_ctx_frozen_until_overflow(monkeypatch):
    from backend.agents import scrum_agent
    from backend.agents.scrum_agent import ScrumAgent
    from backend.services import workflow_settings as ws_mod

    agent = ScrumAgent("dev", "test-model", "sys")
    ws = {"ollamaNumCtx": 32768, "ollamaNumCtxAdaptive": False}
    monkeypatch.setattr(scrum_agent, "get_workflow_settings", lambda: ws)
    monkeypatch.setattr(ws_mod, "get_workflow_settings", lambda: ws)
    small = [{"role": "user", "content": "hello"}]
    big = [{"role": "user", "content": "x" * 20000}]
    agent._ensure_packed_num_ctx(small)
    first = agent._effective_num_ctx()
    agent._ensure_packed_num_ctx(big)
    assert agent._effective_num_ctx() == first
