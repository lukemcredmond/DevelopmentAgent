"""Smoke tests for hierarchical subtask todos."""

from backend.bootstrap import initialize
from backend.services.workflow_settings import reset_workflow_settings


def _empty_board(**lanes):
    base = {
        "Backlog": [],
        "Refinement": [],
        "In Progress": [],
        "Needs PO": [],
        "Needs User": [],
        "QA": [],
        "Done": [],
    }
    base.update(lanes)
    return base


def test_append_subtasks_blocks_parent_until_done():
    from backend import state
    from backend.agents.task_context import init_new_task, next_claimable_backlog_task
    from backend.services.subtask_service import append_subtasks

    initialize()
    reset_workflow_settings()
    parent = init_new_task({"id": "T-PARENT", "title": "Main todo", "description": "d"})
    state.SHARED_BOARD = _empty_board(**{"In Progress": [parent]})

    result = append_subtasks(
        "T-PARENT",
        [
            {"title": "Run lint", "description": "flutter analyze", "executionOrder": 1},
            {"title": "Fix errors", "description": "resolve lint", "executionOrder": 2},
        ],
    )
    assert "Added 2 subtask" in result
    parent = next(t for lane in state.SHARED_BOARD.values() for t in lane if t["id"] == "T-PARENT")
    assert len(parent.get("subtaskIds") or []) == 2
    assert len(parent.get("blockedBy") or []) == 2

    backlog = state.SHARED_BOARD.get("Backlog", [])
    assert len(backlog) == 2
    assert backlog[0]["executionOrder"] == 1
    assert backlog[1]["blockedBy"] == [backlog[0]["id"]]

    claimed = next_claimable_backlog_task()
    assert claimed is not None
    assert claimed["id"] == backlog[0]["id"]


def test_subtask_gate_blocks_dev_advance():
    from backend import state
    from backend.agents.task_context import init_new_task
    from backend.services.sprint_service import dev_gate_blocks_advance
    from backend.services.subtask_service import append_subtasks

    initialize()
    reset_workflow_settings()
    parent = init_new_task({"id": "T-GATE", "title": "Gate test", "description": "d"})
    state.SHARED_BOARD = _empty_board(**{"In Progress": [parent]})
    append_subtasks("T-GATE", [{"title": "Child", "description": "work", "executionOrder": 1}])

    parent = next(t for lane in state.SHARED_BOARD.values() for t in lane if t["id"] == "T-GATE")
    blocked, reason = dev_gate_blocks_advance(parent)
    assert blocked is True
    assert "subtask" in reason.lower()


def test_on_subtask_completed_unblocks_parent():
    from backend import state
    from backend.agents.task_context import init_new_task
    from backend.services.board_service import move_board_stage
    from backend.services.subtask_service import append_subtasks, on_subtask_completed

    initialize()
    reset_workflow_settings()
    parent = init_new_task({"id": "T-UNB", "title": "Parent", "description": "d"})
    state.SHARED_BOARD = _empty_board(**{"In Progress": [parent]})
    append_subtasks("T-UNB", [{"title": "Only child", "description": "x", "executionOrder": 1}])
    child_id = state.SHARED_BOARD["Backlog"][0]["id"]
    move_board_stage(child_id, "Done")
    on_subtask_completed(child_id)

    parent = next(t for lane in state.SHARED_BOARD.values() for t in lane if t["id"] == "T-UNB")
    assert child_id not in (parent.get("blockedBy") or [])


def test_escape_subtask_loop_moves_to_needs_po():
    from backend import state
    from backend.agents.task_context import init_new_task, get_task_lane
    from backend.services.subtask_service import append_subtasks, escape_subtask_loop

    initialize()
    reset_workflow_settings()
    parent = init_new_task({"id": "T-ESC", "title": "Stuck", "description": "d"})
    state.SHARED_BOARD = _empty_board(**{"In Progress": [parent]})
    append_subtasks("T-ESC", [{"title": "A", "description": "a", "executionOrder": 1}])

    result = escape_subtask_loop("T-ESC", mode="needs_po")
    assert "Needs PO" in result
    assert get_task_lane("T-ESC") == "Needs PO"


def test_nested_subtask_depth_limit():
    from backend import state
    from backend.agents.task_context import init_new_task
    from backend.services.subtask_service import append_subtasks
    from backend.services.workflow_settings import save_workflow_settings

    initialize()
    reset_workflow_settings()
    save_workflow_settings({"maxSubtaskDepth": 2})
    root = init_new_task({"id": "T-ROOT", "title": "Root", "description": "d"})
    state.SHARED_BOARD = _empty_board(**{"In Progress": [root]})
    append_subtasks("T-ROOT", [{"title": "L1", "description": "l1", "executionOrder": 1}])
    l1_id = state.SHARED_BOARD["Backlog"][0]["id"]
    state.SHARED_BOARD["In Progress"].append(state.SHARED_BOARD["Backlog"].pop(0))
    state.SHARED_BOARD["In Progress"][-1]["status"] = "In Progress"

    append_subtasks(l1_id, [{"title": "L2", "description": "l2", "executionOrder": 1}])
    l2_id = state.SHARED_BOARD["Backlog"][0]["id"]
    state.SHARED_BOARD["In Progress"].append(state.SHARED_BOARD["Backlog"].pop(0))

    result = append_subtasks(l2_id, [{"title": "L3", "description": "too deep", "executionOrder": 1}])
    assert result.startswith("Error")
    assert "depth" in result.lower()
