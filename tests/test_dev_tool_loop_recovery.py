"""Tool-loop deadlock recovery and related policy helpers."""

from unittest.mock import patch

from backend.services.dev_tool_loop_recovery import (
    arm_task_for_tool_loop_recovery,
    build_blocked_board_recovery_hint,
    dev_tool_loop_deadlock_active,
    filter_developer_step_tools,
    handle_developer_board_blocked_retry,
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
    assert is_premature_lane_update({"target_lane": "Done"})
    assert not is_premature_lane_update({"stage": "In Progress"})


def test_filter_developer_step_tools_hides_board_on_oracle_fail(monkeypatch):
    tools = [
        {"function": {"name": "apply_patch"}},
        {"function": {"name": "update_board"}},
    ]

    class _Agent:
        _board_blocked_count = 0
        _hide_update_board_tools = False

    with patch(
        "backend.services.dev_tool_loop_recovery.developer_oracle_blocks_lane_advance",
        return_value=True,
    ):
        filtered = filter_developer_step_tools(tools, "T-1")
    names = {(t.get("function") or {}).get("name") for t in filtered}
    assert "update_board" not in names
    assert "apply_patch" in names


def test_handle_developer_board_blocked_retry_stops_at_cap():
    agent = type("A", (), {"_board_blocked_count": 0, "_hide_update_board_tools": False})()

    with patch(
        "backend.services.dev_tool_loop_recovery.arm_task_for_tool_loop_recovery",
        return_value="lib/main.dart",
    ):
        with patch(
            "backend.services.dev_tool_loop_recovery.max_premature_board_retries",
            return_value=2,
        ):
            assert handle_developer_board_blocked_retry(
                agent,
                "T-1",
                "Error: Oracle FAIL — cannot mark Done",
            ) is None
            assert agent._board_blocked_count == 1
            stop = handle_developer_board_blocked_retry(
                agent,
                "T-1",
                "Error: Oracle FAIL — cannot mark Done",
            )
    assert stop is not None
    assert "cap 2" in stop


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
