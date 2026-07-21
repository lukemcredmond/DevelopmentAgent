"""Sprint dependency deadlock: prefer unblockers, escalate cycles, tighten has_sprint_work."""

from unittest.mock import patch

from backend import state
from backend.agents.task_context import (
    detect_blocked_by_issues,
    init_new_task,
    task_dependencies_met,
)
from backend.bootstrap import initialize
from backend.services.sprint_service import (
    _try_backlog_handler,
    has_sprint_work,
    run_sprint_step,
)


def _empty_board():
    initialize()
    state.SHARED_BOARD.clear()
    for lane in (
        "Backlog",
        "In Progress",
        "Needs User",
        "Needs PO",
        "QA",
        "Done",
        "Refinement",
        "Code Review",
        "Features",
    ):
        state.SHARED_BOARD[lane] = []


def test_prefer_dependency_child_over_blocked_parent():
    _empty_board()
    with patch(
        "backend.services.workflow_settings.get_workflow_settings",
        return_value={"requireBacklogRefinement": False, "prioritizeImplementationOverRefinement": True},
    ):
        child_a = init_new_task(
            {
                "id": "TASK-A",
                "title": "Dep A",
                "description": "first",
                "status": "Backlog",
                "priority": 50,
                "requiresDev": True,
            }
        )
        child_b = init_new_task(
            {
                "id": "TASK-B",
                "title": "Dep B",
                "description": "second",
                "status": "Backlog",
                "priority": 50,
                "requiresDev": True,
            }
        )
        parent = init_new_task(
            {
                "id": "TASK-PARENT",
                "title": "Parent",
                "description": "blocked",
                "status": "Backlog",
                "priority": 1,
                "blockedBy": ["TASK-A", "TASK-B"],
                "requiresDev": True,
            }
        )
        state.SHARED_BOARD["Backlog"] = [parent, child_a, child_b]

        handler, task = _try_backlog_handler()
        assert handler == "dev"
        assert task is not None
        assert task["id"] in ("TASK-A", "TASK-B")
        assert task["id"] != "TASK-PARENT"
        lane = next(
            (
                ln
                for ln, tasks in state.SHARED_BOARD.items()
                for t in tasks
                if t.get("id") == task["id"]
            ),
            None,
        )
        assert lane == "In Progress"


def test_cycle_escalates_to_needs_user():
    _empty_board()
    with patch(
        "backend.services.workflow_settings.get_workflow_settings",
        return_value={"requireBacklogRefinement": False},
    ):
        a = init_new_task(
            {
                "id": "TASK-CYCLE-A",
                "title": "A",
                "description": "a",
                "status": "Backlog",
                "blockedBy": ["TASK-CYCLE-B"],
                "requiresDev": True,
            }
        )
        b = init_new_task(
            {
                "id": "TASK-CYCLE-B",
                "title": "B",
                "description": "b",
                "status": "Backlog",
                "blockedBy": ["TASK-CYCLE-A"],
                "requiresDev": True,
            }
        )
        state.SHARED_BOARD["Backlog"] = [a, b]

        issues = detect_blocked_by_issues(a)
        assert issues["cycle"] is True

        handler, task = _try_backlog_handler()
        assert handler is None
        assert task is None
        needs = {t["id"] for t in state.SHARED_BOARD.get("Needs User", [])}
        assert "TASK-CYCLE-A" in needs or "TASK-CYCLE-B" in needs


def test_has_sprint_work_false_when_only_blocked_without_claimable_deps():
    _empty_board()
    with patch(
        "backend.services.workflow_settings.get_workflow_settings",
        return_value={"requireBacklogRefinement": False, "requireCodeReview": False},
    ):
        parent = init_new_task(
            {
                "id": "TASK-ONLY",
                "title": "Blocked forever",
                "description": "x",
                "status": "Backlog",
                "blockedBy": ["TASK-MISSING"],
                "requiresDev": True,
            }
        )
        state.SHARED_BOARD["Backlog"] = [parent]
        assert not task_dependencies_met(parent)
        # Missing dep escalates; after handler run, no claimable work.
        _try_backlog_handler()
        assert has_sprint_work() is False or state.SHARED_BOARD.get("Needs User")


def test_has_sprint_work_true_when_child_claimable():
    _empty_board()
    with patch(
        "backend.services.workflow_settings.get_workflow_settings",
        return_value={"requireBacklogRefinement": False, "requireCodeReview": False},
    ):
        child = init_new_task(
            {
                "id": "TASK-CHILD",
                "title": "Child",
                "description": "c",
                "status": "Backlog",
                "requiresDev": True,
            }
        )
        parent = init_new_task(
            {
                "id": "TASK-P",
                "title": "Parent",
                "description": "p",
                "status": "Backlog",
                "blockedBy": ["TASK-CHILD"],
                "priority": 1,
                "requiresDev": True,
            }
        )
        state.SHARED_BOARD["Backlog"] = [parent, child]
        assert has_sprint_work() is True


def test_run_sprint_step_does_not_noop_on_blocked():
    _empty_board()
    with patch(
        "backend.services.workflow_settings.get_workflow_settings",
        return_value={
            "requireBacklogRefinement": False,
            "prioritizeImplementationOverRefinement": True,
            "requireCodeReview": False,
            "pauseSprintOnNeedsUser": False,
        },
    ):
        child = init_new_task(
            {
                "id": "TASK-D1",
                "title": "Dep",
                "description": "d",
                "status": "Backlog",
                "requiresDev": True,
            }
        )
        parent = init_new_task(
            {
                "id": "TASK-DP",
                "title": "Parent",
                "description": "p",
                "status": "Backlog",
                "blockedBy": ["TASK-D1"],
                "priority": 1,
                "requiresDev": True,
            }
        )
        state.SHARED_BOARD["Backlog"] = [parent, child]

        with patch("backend.services.sprint_service._run_developer_step") as mock_dev:
            mock_dev.return_value = None
            run_sprint_step("brief", "http://localhost:11434")
            assert mock_dev.called
            claimed_id = mock_dev.call_args[0][0].get("id")
            assert claimed_id == "TASK-D1"
