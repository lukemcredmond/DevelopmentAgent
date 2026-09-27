"""Plan outline PO tool filtering."""

from backend.agents.scrum_agent import po_execute_step_tools
from backend.services.sprint_service import PLANNING_OUTLINE_TASK_ID


def test_plan_outline_explore_excludes_semantic_and_graph():
    registry = [
        {"type": "function", "function": {"name": "list_dir"}},
        {"type": "function", "function": {"name": "semantic_search"}},
        {"type": "function", "function": {"name": "graph_query"}},
        {"type": "function", "function": {"name": "read_file"}},
    ]
    tools, json_only = po_execute_step_tools(
        registry,
        role="Product Owner",
        task_id=PLANNING_OUTLINE_TASK_ID,
    )
    names = {(t.get("function") or {}).get("name") for t in tools}
    assert "list_dir" in names
    assert "read_file" in names
    assert "semantic_search" not in names
    assert "graph_query" not in names
    assert json_only is False


def test_plan_outline_write_has_no_tools():
    registry = [{"type": "function", "function": {"name": "list_dir"}}]
    tools, json_only = po_execute_step_tools(
        registry,
        role="Product Owner",
        task_id=PLANNING_OUTLINE_TASK_ID,
        plan_outline_no_tools=True,
    )
    assert tools == []
    assert json_only is True
