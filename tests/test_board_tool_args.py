"""Board tool argument repair for recovered PO tool calls."""

from backend import state
from backend.services.board_tool_args import repair_sprint_board_tool_arguments


def test_repair_update_board_fills_task_id_and_target_lane():
    state.ACTIVE_SPRINT_TASK_ID = "TASK-ACTIVE"
    args = repair_sprint_board_tool_arguments("update_board", {}, task_id=None)
    assert args["task_id"] == "TASK-ACTIVE"
    assert args["target_lane"] == "In Progress"


def test_repair_update_board_preserves_explicit_args():
    args = repair_sprint_board_tool_arguments(
        "update_board",
        {"task_id": "T-1", "target_lane": "Needs PO"},
        task_id="TASK-OTHER",
    )
    assert args["task_id"] == "T-1"
    assert args["target_lane"] == "Needs PO"


def test_repair_ignores_non_board_tools():
    assert repair_sprint_board_tool_arguments("read_file", {"path": "x.py"}) == {
        "path": "x.py"
    }
