"""PO must not treat sprint steps like empty chat after exploration tools."""

from backend import state
from backend.agents.scrum_agent import (
    _looks_like_po_idle_greeting,
    _looks_like_po_implementation_plan,
    _looks_like_po_work_product,
    _po_step_should_reject_text_only,
)


def test_po_idle_greeting_detected():
    assert _looks_like_po_idle_greeting(
        "I am ready to act as your Product Owner. Please provide the product brief or any new feature."
    )


def test_po_work_json_not_idle():
    assert _looks_like_po_work_product(
        '{"description": "x", "acceptanceCriteria": ["a"]}'
    )


def test_po_rejects_text_after_list_dir_only():
    state.ACTIVE_SPRINT_AGENT = "Product Owner"
    try:
        assert _po_step_should_reject_text_only(
            "Ready to act as your PO — share the brief.",
            {"list_dir"},
            "TASK-1",
        )
    finally:
        state.ACTIVE_SPRINT_AGENT = None


def test_po_accepts_json_after_tools():
    state.ACTIVE_SPRINT_AGENT = "Product Owner"
    try:
        assert not _po_step_should_reject_text_only(
            '{"description": "done", "acceptanceCriteria": ["ac1"]}',
            {"list_dir", "grep"},
            "TASK-1",
        )
    finally:
        state.ACTIVE_SPRINT_AGENT = None


def test_po_rejects_dev_step_list_in_needs_po(monkeypatch):
    from backend.agents.task_context import init_new_task

    state.ACTIVE_SPRINT_AGENT = "Product Owner"
    state.SHARED_BOARD.setdefault("Needs PO", [])
    task = init_new_task({"id": "T-PO-PLAN", "title": "Meal backup", "description": "d", "status": "Needs PO"})
    state.SHARED_BOARD["Needs PO"] = [task]
    text = (
        "Develop the backup functionality for meal data. Follow these steps:\n"
        "1. Add export service\n2. Wire UI\n"
    )
    try:
        assert _looks_like_po_implementation_plan(text, "T-PO-PLAN")
        assert _po_step_should_reject_text_only(text, set(), "T-PO-PLAN")
    finally:
        state.ACTIVE_SPRINT_AGENT = None
        state.SHARED_BOARD["Needs PO"] = []
