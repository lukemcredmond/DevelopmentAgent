"""Two-phase PO planning (outline then backlog)."""

from unittest.mock import patch

from backend.bootstrap import initialize


@patch("backend.services.sprint_service.agent_po")
def test_run_po_plan_outline_stores_outline(mock_po):
    initialize()
    from backend import state
    from backend.services.sprint_service import run_po_plan_outline

    mock_po.execute_step.return_value = "## Summary\nTest plan\n"
    outline = run_po_plan_outline("Build a todo app", "http://localhost:11434")
    assert "Summary" in outline
    assert state.PROJECT_PLAN_OUTLINE == outline


@patch("backend.services.sprint_service.agent_po")
@patch("backend.services.sprint_service._append_tasks", return_value=2)
def test_run_po_plan_backlog_uses_outline(mock_append, mock_po):
    initialize()
    from backend import state
    from backend.services.sprint_service import run_po_plan_backlog

    state.PROJECT_PLAN_OUTLINE = "## Summary\nPlan\n"
    mock_po.execute_step.return_value = '[{"title": "Task A", "description": "d", "acceptanceCriteria": ["a"]}]'
    count = run_po_plan_backlog("Build app", "http://localhost:11434")
    assert count == 2
    mock_po.execute_step.assert_called_once()
    assert "Approved plan outline" in mock_po.execute_step.call_args[0][0]
