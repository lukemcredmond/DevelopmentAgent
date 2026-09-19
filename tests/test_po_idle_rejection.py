"""PO must not treat sprint steps like empty chat after exploration tools."""

from backend import state
from backend.agents.scrum_agent import (
    _looks_like_po_idle_greeting,
    _looks_like_po_implementation_plan,
    _looks_like_po_work_product,
    _po_rejection_system_message,
    _po_step_should_reject_text_only,
    po_execute_step_tools,
)
from backend.services.sprint_service import PLANNING_BACKLOG_TASK_ID, PLANNING_OUTLINE_TASK_ID


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


def test_po_planning_accepts_markdown_after_explore_tools():
    state.ACTIVE_SPRINT_AGENT = "Product Owner"
    plan = (
        "## Summary\nA meal planner app.\n\n## Approach\nScaffold core modules first.\n\n"
        "## Proposed epics\n- Recipes — browse and save recipes\n"
    )
    try:
        assert not _po_step_should_reject_text_only(
            plan, {"list_dir", "read_file"}, PLANNING_OUTLINE_TASK_ID
        )
    finally:
        state.ACTIVE_SPRINT_AGENT = None


def test_po_planning_still_rejects_idle_after_explore():
    state.ACTIVE_SPRINT_AGENT = "Product Owner"
    try:
        assert _po_step_should_reject_text_only(
            "I am ready to act as your Product Owner. Please provide the product brief.",
            {"list_dir"},
            PLANNING_OUTLINE_TASK_ID,
        )
    finally:
        state.ACTIVE_SPRINT_AGENT = None


def test_po_backlog_rejects_markdown_outline_after_explore():
    state.ACTIVE_SPRINT_AGENT = "Product Owner"
    plan = "## Summary\nA meal planner.\n\n## Proposed epics\n- Recipes\n"
    try:
        assert _po_step_should_reject_text_only(
            plan, {"list_dir", "read_file"}, PLANNING_BACKLOG_TASK_ID
        )
    finally:
        state.ACTIVE_SPRINT_AGENT = None


def test_po_backlog_accepts_epics_json_after_explore():
    state.ACTIVE_SPRINT_AGENT = "Product Owner"
    payload = (
        '{"epics":[{"title":"Recipes","description":"d","children":'
        '[{"title":"List recipes","description":"d","acceptanceCriteria":["ok"]}]}]}'
    )
    try:
        assert not _po_step_should_reject_text_only(
            payload, {"list_dir"}, PLANNING_BACKLOG_TASK_ID
        )
    finally:
        state.ACTIVE_SPRINT_AGENT = None


def test_po_backlog_rejection_message_for_markdown_outline():
    plan = "## Summary\nPlan\n\n## Proposed epics\n- Epic A\n"
    msg = _po_rejection_system_message(plan, PLANNING_BACKLOG_TASK_ID)
    assert "JSON object" in msg
    assert "epics" in msg


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


def test_planning_backlog_uses_no_tools():
    registry_tools = [
        {"type": "function", "function": {"name": "read_file"}},
        {"type": "function", "function": {"name": "list_dir"}},
        {"type": "function", "function": {"name": "update_board"}},
    ]
    tools, json_only = po_execute_step_tools(
        registry_tools,
        role="Product Owner",
        task_id=PLANNING_BACKLOG_TASK_ID,
    )
    assert json_only is True
    assert tools == []


def test_planning_outline_keeps_readonly_tools():
    registry_tools = [
        {"type": "function", "function": {"name": "read_file"}},
        {"type": "function", "function": {"name": "update_board"}},
    ]
    tools, json_only = po_execute_step_tools(
        registry_tools,
        role="Product Owner",
        task_id=PLANNING_OUTLINE_TASK_ID,
    )
    assert json_only is False
    assert len(tools) == 1
    assert tools[0]["function"]["name"] == "read_file"
