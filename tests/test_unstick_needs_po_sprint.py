"""Needs PO queue, stall reset, and sprint work detection."""

from backend import state
from backend.agents.task_context import init_new_task
from backend.bootstrap import initialize
from backend.services.dev_refusal_triage import defer_dev_card, diagnose_dev_refusal
from backend.services.sprint_speed_gates import (
    clear_sprint_stall_fields,
    needs_po_should_skip_auto,
    reset_dev_cycle_latch,
    task_needs_sprint_stall_reset,
)
from backend.services.sprint_service import (
    _first_runnable_needs_po,
    has_sprint_work,
)
from backend.services.po_task_paths import stamp_write_paths_on_po_task


def _board(*lanes):
    state.SHARED_BOARD.clear()
    for lane in (
        "Backlog",
        "In Progress",
        "Needs PO",
        "Needs Dev",
        "Blocked",
        "Needs User",
        "QA",
        "Done",
    ):
        state.SHARED_BOARD[lane] = []
    for lane, tasks in lanes:
        state.SHARED_BOARD[lane] = tasks


def test_defer_to_needs_po_does_not_set_po_auto_skip():
    initialize()
    task = init_new_task(
        {
            "id": "T-POQ",
            "title": "Add meal list UI",
            "description": "d",
            "scope": "s",
            "testPlan": "t",
            "status": "In Progress",
        }
    )
    _board(("In Progress", [task]))
    diagnosis = diagnose_dev_refusal(
        task,
        exit_reason="dev_step_wall",
        agent_result="",
    )
    diagnosis["recommendedAction"] = "route_needs_po_split"
    diagnosis["cause"] = "missing_edit_target"
    assert defer_dev_card("T-POQ", diagnosis) is True
    live = None
    for lane_tasks in state.SHARED_BOARD.values():
        for t in lane_tasks:
            if str(t.get("id")) == "T-POQ":
                live = t
    assert live is not None
    assert live.get("status") == "Needs PO"
    assert not live.get("poAutoSkip")
    assert needs_po_should_skip_auto(live) is False
    assert _first_runnable_needs_po() is not None


def test_reset_sprint_stalls_clears_po_auto_skip():
    initialize()
    task = init_new_task(
        {
            "id": "T-RST",
            "title": "Card",
            "description": "d",
            "scope": "s",
            "testPlan": "t",
            "status": "Needs PO",
            "poAutoSkip": True,
            "poAutoSkipReason": "stuck",
            "consecutiveBadExits": 4,
        }
    )
    assert task_needs_sprint_stall_reset(task) is True
    clear_sprint_stall_fields(task)
    assert not task.get("poAutoSkip")
    assert int(task.get("consecutiveBadExits") or 0) == 0


def test_has_sprint_work_with_needs_dev_lane():
    initialize()
    task = init_new_task(
        {
            "id": "T-ND",
            "title": "Implement lib/screens/home.dart",
            "description": "Build home screen",
            "scope": "s",
            "testPlan": "widget test",
            "status": "Needs Dev",
            "requiresDev": True,
            "workType": "implementation",
            "writePaths": ["lib/screens/home.dart"],
        }
    )
    _board(("Needs Dev", [task]))
    assert has_sprint_work(state.SHARED_BOARD) is True


def test_has_sprint_work_blocked_deps_met():
    initialize()
    done = init_new_task(
        {
            "id": "T-DONE",
            "title": "Dep",
            "description": "d",
            "scope": "s",
            "testPlan": "t",
            "status": "Done",
        }
    )
    blocked = init_new_task(
        {
            "id": "T-BLK",
            "title": "Parent",
            "description": "d",
            "scope": "s",
            "testPlan": "t",
            "status": "Blocked",
            "blockedBy": ["T-DONE"],
        }
    )
    _board(("Done", [done]), ("Blocked", [blocked]))
    assert has_sprint_work(state.SHARED_BOARD) is True


def test_stamp_write_paths_from_title():
    task = {"title": "Edit lib/providers/meals_provider.dart for Riverpod"}
    added = stamp_write_paths_on_po_task(task)
    assert "lib/providers/meals_provider.dart" in (task.get("writePaths") or [])
    assert added
