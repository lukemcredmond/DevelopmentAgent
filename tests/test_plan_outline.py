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
def test_run_po_plan_outline_max_iterations_not_stored(mock_po):
    initialize()
    from backend import state
    from backend.services.sprint_service import run_po_plan_outline

    state.PROJECT_PLAN_OUTLINE = ""
    mock_po.execute_step.return_value = (
        "Max tool iterations reached without completing the task."
    )
    outline = run_po_plan_outline("Build a todo app", "http://localhost:11434")
    assert outline == ""
    assert not (state.PROJECT_PLAN_OUTLINE or "").startswith("Max tool iterations")


@patch("backend.services.sprint_service.agent_po")
def test_run_po_plan_outline_rejects_qwen_tool_xml(mock_po):
    initialize()
    from backend import state
    from backend.services.sprint_service import run_po_plan_outline

    state.PROJECT_PLAN_OUTLINE = ""
    mock_po.execute_step.return_value = (
        "I'll start by exploring the existing codebase.\n\n"
        "<tool_call>\n<function=list_dir>\n<parameter=path>\n.\n</parameter>\n"
        "</function>\n</tool_call>\n"
    )
    outline = run_po_plan_outline("Build a todo app", "http://localhost:11434")
    assert outline == ""
    assert "<function=" not in (state.PROJECT_PLAN_OUTLINE or "")


@patch("backend.services.sprint_service.agent_po")
def test_run_po_plan_outline_llm_call_failed_skips_stub(mock_po):
    initialize()
    from backend import state
    from backend.services.simulation_gate import get_pending_simulation_public
    from backend.services.sprint_service import run_po_plan_outline
    from backend.services.workflow_settings import save_workflow_settings

    state.PENDING_SIMULATION = None
    save_workflow_settings({"confirmSimulationFallback": True})
    mock_po.execute_step.return_value = "LLM_CALL_FAILED: timeout"
    outline = run_po_plan_outline("Build a todo app", "http://localhost:11434")
    assert outline == ""
    assert get_pending_simulation_public() is None


@patch("backend.services.sprint_service.agent_po")
def test_run_po_plan_outline_offline_stub_lists_many_epics(mock_po):
    initialize()
    from backend.services.sprint_service import run_po_plan_outline
    from backend.services.workflow_settings import save_workflow_settings

    save_workflow_settings({"confirmSimulationFallback": False})
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
    assert "epic" in prompt.lower() and "children" in prompt.lower()
    mock_epics.assert_called_once()


@patch("backend.services.sprint_service.publish_sprint_progress")
@patch("backend.services.sprint_service.agent_po")
@patch(
    "backend.services.sprint_service.apply_plan_epics_from_po_output",
    return_value={"epicCount": 1, "childCount": 2, "reusedEpicIds": [], "epicIds": ["FEAT-1"], "childIds": ["T-1", "T-2"]},
)
def test_run_po_plan_backlog_publishes_sprint_progress(mock_epics, mock_po, mock_progress):
    initialize()
    from backend import state
    from backend.services.sprint_service import PLANNING_BACKLOG_TASK_ID, run_po_plan_backlog

    state.PROJECT_PLAN_OUTLINE = "## Summary\nPlan\n"
    mock_po.execute_step.return_value = (
        '{"epics":[{"title":"Epic A","description":"d","children":[{"title":"Task A","description":"d","acceptanceCriteria":["a"]}]}]}'
    )
    count = run_po_plan_backlog("Build app", "http://localhost:11434")
    assert count == 2
    assert mock_progress.call_count >= 2
    start_call = mock_progress.call_args_list[0].kwargs
    assert start_call["task_id"] == PLANNING_BACKLOG_TASK_ID
    assert start_call["phase"] == "po_plan"
    done_call = mock_progress.call_args_list[-1].kwargs
    assert done_call["phase"] == "done"
    assert done_call["task_id"] == PLANNING_BACKLOG_TASK_ID


@patch("backend.services.sprint_service.agent_po")
def test_run_po_plan_backlog_rejects_markdown_outline(mock_po):
    initialize()
    from backend import state
    from backend.services.sprint_service import run_po_plan_backlog

    state.PROJECT_PLAN_OUTLINE = "## Summary\nPlan\n"
    state.SYSTEM_LOGS.clear()
    mock_po.execute_step.return_value = (
        "## Summary\nMeal planner.\n\n## Approach\nCore modules.\n\n## Proposed epics\n- Recipes\n"
    )
    count = run_po_plan_backlog("Build app", "http://localhost:11434")
    assert count == 0
    errors = [e for e in state.SYSTEM_LOGS if e.get("type") == "error"]
    assert any("markdown outline instead of JSON epics" in (e.get("text") or "") for e in errors)


def test_build_epics_json_from_plan_outline_parses_bullets():
    from backend.services.feature_service import (
        build_epics_json_from_plan_outline,
        looks_like_usable_plan_epics,
    )

    outline = (
        "## Summary\nMeal shopping list app.\n\n## Approach\nFlutter scaffold.\n\n"
        "## Proposed epics\n"
        "- Recipes — browse and save recipes\n"
        "- Shopping list — add items from recipes\n"
        "1. Meal planner — weekly plan view\n"
    )
    payload = build_epics_json_from_plan_outline(outline)
    assert looks_like_usable_plan_epics(payload)
    assert "Recipes" in payload
    assert "Shopping list" in payload
    assert "Meal planner" in payload


@patch("backend.services.sprint_service.apply_plan_epics_from_po_output")
@patch("backend.services.sprint_service.agent_po")
def test_run_po_plan_backlog_fallback_from_outline_when_llm_returns_loop_stop(
    mock_po,
    mock_apply,
):
    initialize()
    from backend import state
    from backend.services.sprint_service import run_po_plan_backlog

    state.PROJECT_PLAN_OUTLINE = (
        "## Summary\nMeal shopping list app.\n\n## Proposed epics\n"
        "- Recipes — browse and save recipes\n"
        "- Shopping list — add items from recipes\n"
    )
    state.SYSTEM_LOGS.clear()
    mock_po.execute_step.return_value = (
        "Stopped: loop detected — 'read_file' invoked 4 time(s) this step with identical arguments "
        "(doc/plan.md). Tool output and workspace are unchanged."
    )
    mock_apply.return_value = {
        "epicCount": 2,
        "childCount": 4,
        "reusedEpicIds": [],
        "epicIds": ["FEAT-1", "FEAT-2"],
        "childIds": ["T-1", "T-2", "T-3", "T-4"],
    }
    count = run_po_plan_backlog("Build app", "http://localhost:11434")
    assert count == 4
    mock_apply.assert_called_once()
    warnings = [e for e in state.SYSTEM_LOGS if e.get("type") == "warning"]
    assert any("Created cards from outline" in (e.get("text") or "") for e in warnings)


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
    assert "epic" in prompt.lower() and "children" in prompt.lower()


@patch("backend.services.sprint_service.agent_po")
def test_run_po_plan_llm_call_failed_does_not_open_simulation(mock_po):
    initialize()
    from backend import state
    from backend.services.simulation_gate import get_pending_simulation_public
    from backend.services.sprint_service import run_po_plan
    from backend.services.workflow_settings import save_workflow_settings

    state.PENDING_SIMULATION = None
    save_workflow_settings({"confirmSimulationFallback": True})
    mock_po.execute_step.return_value = "LLM_CALL_FAILED: HTTP 400 tools not allowed"
    mock_po._last_chat_error = "HTTP 400 tools not allowed"
    planned = run_po_plan("Build a todo app", "http://localhost:11434")
    assert planned is False
    assert get_pending_simulation_public() is None


@patch("backend.services.sprint_service.agent_po")
def test_run_po_plan_unhealthy_provider_defers_simulation(mock_po):
    initialize()
    from backend import state
    from backend.services.simulation_gate import get_pending_simulation_public
    from backend.services.sprint_service import run_po_plan
    from backend.services.workflow_settings import save_workflow_settings

    state.PENDING_SIMULATION = None
    save_workflow_settings({"confirmSimulationFallback": True})
    mock_po.execute_step.return_value = "SIMULATION_FALLBACK"
    mock_po._last_chat_error = "connection refused"
    planned = run_po_plan("Build a todo app", "http://localhost:11434")
    assert planned is False
    pending = get_pending_simulation_public()
    assert pending is not None
    assert pending["kind"] == "po_backlog"
    assert pending.get("lastChatError") == "connection refused"
