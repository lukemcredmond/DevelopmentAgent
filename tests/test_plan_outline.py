"""Two-phase PO planning (outline then Features from plan)."""

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
    prompt = mock_po.execute_step.call_args[0][0]
    assert "focused product epics" in prompt.lower() or "6–12" in prompt or "6-12" in prompt
    assert "Proposed epics" in prompt


@patch("backend.services.sprint_service.agent_po")
def test_run_po_plan_outline_offline_stub_lists_many_epics(mock_po):
    initialize()
    from backend.services.sprint_service import run_po_plan_outline

    mock_po.execute_step.return_value = "SIMULATION_FALLBACK"
    outline = run_po_plan_outline("Build a todo app", "http://localhost:11434")
    assert "Project setup" in outline
    assert outline.count("\n- ") >= 5


@patch("backend.services.sprint_service.agent_po")
@patch(
    "backend.services.sprint_service.apply_plan_epics_from_po_output",
    return_value={"epicCount": 1, "childCount": 2, "reusedEpicIds": [], "epicIds": ["FEAT-1"], "childIds": ["T-1", "T-2"]},
)
def test_run_po_plan_backlog_uses_outline(mock_epics, mock_po):
    initialize()
    from backend import state
    from backend.services.sprint_service import run_po_plan_backlog

    state.PROJECT_PLAN_OUTLINE = "## Summary\nPlan\n"
    mock_po.execute_step.return_value = (
        '{"epics":[{"title":"Epic A","description":"d","children":[{"title":"Task A","description":"d","acceptanceCriteria":["a"]}]}]}'
    )
    count = run_po_plan_backlog("Build app", "http://localhost:11434")
    assert count == 2
    mock_po.execute_step.assert_called_once()
    prompt = mock_po.execute_step.call_args[0][0]
    assert "Approved plan outline" in prompt
    assert "epics" in prompt.lower()
    assert "focused product epics" in prompt.lower() or "6–12" in prompt or "6-12" in prompt
    assert "dependency" in prompt.lower() or "split vague" in prompt.lower()
    mock_epics.assert_called_once()


@patch("backend.services.sprint_service.agent_po")
def test_run_po_plan_prompt_includes_epic_guidance(mock_po):
    initialize()
    from backend.services.sprint_service import run_po_plan

    mock_po.execute_step.return_value = (
        '{"epics":[{"title":"A","description":"d","children":[{"title":"T","description":"d","acceptanceCriteria":["a"]}]}]}'
    )
    with patch(
        "backend.services.sprint_service._append_po_backlog_from_output",
        return_value=1,
    ):
        run_po_plan("Build a meal planner app", "http://localhost:11434")
    prompt = mock_po.execute_step.call_args[0][0]
    assert "focused product epics" in prompt.lower() or "6–12" in prompt or "6-12" in prompt
    assert "audit" in prompt.lower() or "mega" in prompt.lower() or "split" in prompt.lower()
