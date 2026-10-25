"""Tool-loop deadlock recovery and related policy helpers."""

from unittest.mock import patch

from backend.services.dev_tool_loop_recovery import (
    arm_task_for_tool_loop_recovery,
    build_blocked_board_recovery_hint,
    dev_tool_loop_deadlock_active,
    is_premature_lane_update,
    recover_dev_tool_loop_deadlock,
)
from backend.services.duplicate_tool_policy import tool_loop_deadlock_read_threshold
from backend.services.step_diagnostics import classify_tool_failure


def test_lib_main_title_resolves_for_recovery_hint():
    task = {
        "title": "Scaffold polish — analyzer clean on lib/main and app entry",
    }
    hint = build_blocked_board_recovery_hint(task)
    assert "lib/main.dart" in hint
    assert "apply_patch" in hint


def test_dev_tool_loop_deadlock_active_read_and_board():
    assert not dev_tool_loop_deadlock_active(duplicate_read_same_success=1)
    assert dev_tool_loop_deadlock_active(duplicate_read_same_success=2)
    assert dev_tool_loop_deadlock_active(premature_board_blocked=True)
    assert tool_loop_deadlock_read_threshold() == 2


def test_is_premature_lane_update():
    assert is_premature_lane_update({"stage": "Done"})
    assert is_premature_lane_update({"lane": "QA"})
    assert not is_premature_lane_update({"stage": "In Progress"})


def test_classify_premature_lane_advance_on_blocked_board():
    summary = "blocked duplicate TASK-1 → Done"
    assert classify_tool_failure("update_board", f"[blocked fingerprint] {summary}") == (
        "premature_lane_advance"
    )


def test_arm_task_sets_force_patch_and_lint_source():
    task = {
        "title": "Scaffold polish — analyzer clean on lib/main and app entry",
    }
    path = arm_task_for_tool_loop_recovery(task)
    assert path == "lib/main.dart"
    assert task.get("forcePatchNextDevStep") is True
    assert task.get("lintSourceFile") == "lib/main.dart"


def test_should_force_patch_after_duplicate_tool_exit():
    from backend.services.sprint_speed_gates import should_force_patch_next_dev_step

    task = {"lastStepOutcome": {"exitReason": "duplicate_tool"}}
    assert should_force_patch_next_dev_step(task)


def test_recover_dev_tool_loop_deadlock_arms_task():
    from backend import state
    from backend.agents.task_context import init_new_task
    from backend.bootstrap import initialize

    initialize()
    state.SHARED_BOARD.clear()
    for lane in ("Needs PO", "In Progress", "Backlog"):
        state.SHARED_BOARD[lane] = []
    task = init_new_task(
        {
            "id": "T-RECOVER",
            "title": "Scaffold polish — analyzer clean on lib/main and app entry",
            "description": "d",
            "status": "Needs PO",
        }
    )
    state.SHARED_BOARD["Needs PO"] = [task]

    with patch("backend.services.backup_model.arm_backup_for_agent") as arm:
        with patch("backend.services.backup_model.should_arm_from_exit_reason", return_value=True):
            recover_dev_tool_loop_deadlock("T-RECOVER")
    arm.assert_called_once()
    live = next(t for t in state.SHARED_BOARD["Needs PO"] if t.get("id") == "T-RECOVER")
    assert live.get("forcePatchNextDevStep") is True
    assert live.get("lintSourceFile") == "lib/main.dart"
