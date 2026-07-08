"""Smoke tests for backlog refinement stage."""

from backend.bootstrap import initialize
from backend.services.workflow_settings import reset_workflow_settings, save_workflow_settings


def _reset_workflow_settings():
    reset_workflow_settings()


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


def test_new_task_lane_refinement_when_toggle_on():
    from backend.services.board_service import _new_task_lane

    initialize()
    _reset_workflow_settings()
    save_workflow_settings({"requireBacklogRefinement": True, "requireBacklogApproval": False})
    assert _new_task_lane() == "Refinement"
    save_workflow_settings({"requireBacklogRefinement": True, "requireBacklogApproval": True})
    assert _new_task_lane() == "Pending Approval"
    _reset_workflow_settings()


def test_next_claimable_skips_unrefined_backlog():
    from backend import state
    from backend.agents.task_context import init_new_task, next_claimable_backlog_task

    initialize()
    _reset_workflow_settings()
    save_workflow_settings({"requireBacklogRefinement": True})
    unrefined = init_new_task(
        {"id": "T-UR", "title": "Unrefined", "description": "d", "refinementComplete": False}
    )
    refined = init_new_task(
        {"id": "T-R", "title": "Refined", "description": "d", "refinementComplete": True}
    )
    state.SHARED_BOARD = _empty_board(Backlog=[unrefined, refined])
    claimed = next_claimable_backlog_task()
    assert claimed is not None
    assert claimed["id"] == "T-R"
    _reset_workflow_settings()


def test_refinement_dev_review_blocks_write_tools():
    from backend import state
    from backend.agents.registry import agent_dev, configure_agent_tools

    initialize()
    state.REFINEMENT_MODE = True
    configure_agent_tools({"requireBacklogRefinement": True})
    names = agent_dev.registry.tool_names()
    assert "write_file" not in names
    assert "apply_patch" not in names
    assert "run_command" not in names
    assert "read_file" in names
    assert "grep" in names
    state.REFINEMENT_MODE = False
    configure_agent_tools()
    _reset_workflow_settings()


def test_refinement_po_marks_complete_moves_to_backlog(monkeypatch):
    from backend import state
    from backend.agents.task_context import init_new_task, init_refinement_fields
    from backend.services import sprint_service

    initialize()
    task = init_new_task({"id": "T-REF", "title": "Refine me", "description": "d"})
    init_refinement_fields(task)
    task["refinementDevReady"] = True
    task["refinementStatus"] = "dev_reviewed"
    task["refinementQuestions"] = ["What auth provider?"]
    state.SHARED_BOARD = _empty_board(Refinement=[task])

    json_response = '{"description": "Updated scope", "acceptanceCriteria": ["User can log in"]}'
    monkeypatch.setattr(
        sprint_service.agent_po,
        "execute_step",
        lambda prompt, max_iterations=8: json_response,
    )
    sprint_service._run_refinement_po_update(dict(task), "brief")

    updated = state.SHARED_BOARD.get("Backlog", [])
    assert any(t["id"] == "T-REF" for t in updated)
    moved = next(t for t in updated if t["id"] == "T-REF")
    assert moved.get("refinementComplete") is True
    _reset_workflow_settings()


def test_sprint_step_prioritizes_backlog_claim_over_refinement_when_enabled(monkeypatch):
    from backend import state
    from backend.agents.task_context import init_new_task, init_refinement_fields
    from backend.services import sprint_service

    initialize()
    _reset_workflow_settings()
    save_workflow_settings(
        {"requireBacklogRefinement": True, "prioritizeImplementationOverRefinement": True}
    )
    ref_task = init_new_task({"id": "T-REF2", "title": "Refinement card", "description": "d"})
    init_refinement_fields(ref_task)
    ref_task["status"] = "Refinement"
    backlog_task = init_new_task(
        {"id": "T-BL", "title": "Ready backlog", "description": "d", "refinementComplete": True}
    )
    backlog_task["status"] = "Backlog"
    state.SHARED_BOARD = _empty_board(Refinement=[ref_task], Backlog=[backlog_task])

    called = []

    monkeypatch.setattr(
        sprint_service,
        "_run_refinement_dev_review",
        lambda t, b: called.append("refinement_dev"),
    )
    monkeypatch.setattr(sprint_service, "_run_developer_step", lambda t, b: called.append("dev"))
    monkeypatch.setattr(sprint_service, "set_project_brief", lambda *a, **k: None)

    sprint_service.run_sprint_step("brief", "http://localhost:11434")
    assert called == ["dev"]
    _reset_workflow_settings()


def test_sprint_step_prioritizes_refinement_over_backlog_when_setting_disabled(monkeypatch):
    from backend import state
    from backend.agents.task_context import init_new_task, init_refinement_fields
    from backend.services import sprint_service

    initialize()
    _reset_workflow_settings()
    save_workflow_settings(
        {"requireBacklogRefinement": True, "prioritizeImplementationOverRefinement": False}
    )
    ref_task = init_new_task({"id": "T-REF2", "title": "Refinement card", "description": "d"})
    init_refinement_fields(ref_task)
    ref_task["status"] = "Refinement"
    backlog_task = init_new_task(
        {"id": "T-BL", "title": "Ready backlog", "description": "d", "refinementComplete": True}
    )
    backlog_task["status"] = "Backlog"
    state.SHARED_BOARD = _empty_board(Refinement=[ref_task], Backlog=[backlog_task])

    called = []

    monkeypatch.setattr(
        sprint_service,
        "_run_refinement_dev_review",
        lambda t, b: called.append("refinement_dev"),
    )
    monkeypatch.setattr(sprint_service, "_run_developer_step", lambda t, b: called.append("dev"))
    monkeypatch.setattr(sprint_service, "set_project_brief", lambda *a, **k: None)

    sprint_service.run_sprint_step("brief", "http://localhost:11434")
    assert called == ["refinement_dev"]
    _reset_workflow_settings()


def test_max_refinement_rounds_escalates_to_needs_po(monkeypatch):
    from backend import state
    from backend.agents.task_context import init_new_task, init_refinement_fields
    from backend.services import sprint_service

    initialize()
    save_workflow_settings({"requireBacklogRefinement": True, "maxRefinementRoundTrips": 2})
    task = init_new_task({"id": "T-MAX", "title": "Stuck refinement", "description": "d"})
    init_refinement_fields(task)
    task["refinementRoundTrips"] = 1
    state.SHARED_BOARD = _empty_board(Refinement=[task])

    not_ready = '{"ready": false, "questions": ["Need more detail on API contract"]}'
    monkeypatch.setattr(
        sprint_service.agent_dev,
        "execute_step",
        lambda prompt, max_iterations=8: not_ready,
    )
    sprint_service._run_refinement_dev_review(dict(task), "brief")

    needs_po = state.SHARED_BOARD.get("Needs PO", [])
    assert any(t["id"] == "T-MAX" for t in needs_po)
    _reset_workflow_settings()
