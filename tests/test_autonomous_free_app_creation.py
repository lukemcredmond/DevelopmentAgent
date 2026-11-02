"""Regression tests for Autonomous Free App Creation plan."""

from types import SimpleNamespace

from backend.bootstrap import initialize
from backend.services.agent_efficiency import (
    is_developer_tool_capable,
    single_model_name,
)
from backend.services.fix_verify_loop import lint_run_is_clean, lint_run_is_toolchain
from backend.services.prompt_budget import PACKED_NUM_CTX_FLOOR, packed_prompt_num_ctx
from backend.services.sprint_service import (
    _maybe_arm_cloud_for_consecutive_stuck_dev_exit,
    _recover_zero_work_stall,
    mark_autonomous_sprint_resume_if_needed,
)
from backend.services.step_diagnostics import StepDiagnosticsTracker


def test_lint_run_is_clean_zero_findings_lint_findings():
    cmd = SimpleNamespace(outcome="lint_findings", diagnostics=[])
    assert lint_run_is_clean(cmd) is True


def test_lint_run_is_clean_ok():
    cmd = SimpleNamespace(outcome="ok", diagnostics=[])
    assert lint_run_is_clean(cmd) is True


def test_lint_run_is_toolchain():
    cmd = SimpleNamespace(outcome="toolchain_error", diagnostics=[])
    assert lint_run_is_toolchain(cmd) is True
    assert lint_run_is_clean(cmd) is False


def test_classify_toolchain_error_on_permission_denied():
    from backend.agents.tool_outcomes import classify_run_command_outcome

    outcome = classify_run_command_outcome(
        "flutter analyze",
        1,
        "update_engine_version.sh: line 71: Permission denied",
    )
    assert outcome == "toolchain_error"


def test_essentialai_rnj_rejected_for_developer():
    assert is_developer_tool_capable("essentialai/rnj-1:latest") is False
    assert is_developer_tool_capable("qwen2.5-coder:7b") is True


def test_single_model_name_never_returns_non_coder():
    from backend import state
    from backend.services import workflow_settings as ws_mod

    initialize()
    ws_mod.save_workflow_settings({"singleModelMode": "auto"})
    state.PRIMARY_MODELS = {"dev": "essentialai/rnj-1:latest"}
    chosen = single_model_name(ws_mod.get_workflow_settings())
    assert is_developer_tool_capable(chosen)


def test_packed_num_ctx_floor_includes_predict():
    assert PACKED_NUM_CTX_FLOOR >= 8192
    msgs = [{"content": "x" * 4000}]
    nctx = packed_prompt_num_ctx(msgs, 32768, num_predict=2048)
    assert nctx >= PACKED_NUM_CTX_FLOOR


def test_log_tool_records_error_field(tmp_path):
    trace = StepDiagnosticsTracker(
        task_id="T1",
        task_title="T",
        agent="Developer",
        lane_before="In Progress",
        file_path=tmp_path / "step.json",
    )
    trace.log_tool("apply_patch", False, "lib/foo.dart (replace 10 chars)", error="old_text not found")
    entry = trace.tools_log[-1]
    assert entry.get("error") == "old_text not found"


def test_system_verify_failure_not_in_tool_failures_budget(tmp_path):
    trace = StepDiagnosticsTracker(
        task_id="T1",
        task_title="T",
        agent="Developer",
        lane_before="In Progress",
        file_path=tmp_path / "step2.json",
    )
    trace.log_tool("system_verify", False, "system_verify flutter analyze (0 findings, lint_findings)")
    assert trace.tool_failures == 0


def test_mark_autonomous_sprint_resume_if_needed():
    from backend import state
    from backend.services import workflow_settings as ws_mod

    initialize()
    ws_mod.save_workflow_settings({"autonomousMode": True, "autoStartSprint": True})
    state.RECOVERY_CONTEXT = {"suggestedAction": "resume", "taskId": "T1"}
    state.SHARED_BOARD["In Progress"] = [
        {"id": "T1", "title": "Work", "acceptanceCriteria": ["a"], "acChecklist": [False]}
    ]
    state.AUTO_RESUME_SPRINT_PENDING = False
    assert mark_autonomous_sprint_resume_if_needed() is True
    assert state.AUTO_RESUME_SPRINT_PENDING is True


def test_arm_cloud_on_consecutive_stuck_exits():
    from backend.services import workflow_settings as ws_mod

    ws_mod.save_workflow_settings(
        {
            "enableCloudDevProvider": True,
            "cloudDevModel": "gpt-test",
            "cloudDevBaseUrl": "https://example.com/v1",
            "cloudDevStuckSteps": 2,
            "cloudDevUseFor": "stuck_only",
        }
    )
    task = {"id": "T-STUCK", "title": "Stuck"}
    _maybe_arm_cloud_for_consecutive_stuck_dev_exit(
        task, "tool_failure_stop", 1, agent="Developer"
    )
    assert task.get("cloudModelStepsRemaining", {}).get("dev", 0) == 0
    _maybe_arm_cloud_for_consecutive_stuck_dev_exit(
        task, "tool_failure_stop", 2, agent="Developer"
    )
    assert int(task.get("cloudModelStepsRemaining", {}).get("dev", 0)) >= 1
    assert task.get("cloudStuckEscapeArmed") is True


def test_zero_work_ladder_cloud_escape(monkeypatch):
    from backend.services import workflow_settings as ws_mod

    ws_mod.save_workflow_settings(
        {
            "enableCloudDevProvider": True,
            "cloudDevModel": "gpt-test",
            "cloudDevBaseUrl": "https://example.com/v1",
            "maxPoRoundTrips": 1,
        }
    )
    task = {
        "id": "T-ZW",
        "title": "Zero work",
        "phaseCycleCapReached": False,
        "poRoundTrips": 5,
    }
    monkeypatch.setattr(
        "backend.services.sprint_service._live_board_task",
        lambda tid: task,
    )
    monkeypatch.setattr(
        "backend.services.sprint_service._should_attempt_stuck_auto_split",
        lambda *a, **k: False,
    )
    action = _recover_zero_work_stall("T-ZW", "explore_budget_exhausted", "brief")
    assert action == "cloud_escape"
    assert task.get("escapeHatchAttempted") is True
