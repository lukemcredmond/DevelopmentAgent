"""Prior PO clarification attempts are injected into the next sprint prompt."""

from backend import state
from backend.agents.task_context import init_new_task, record_task_decision
from backend.services.sprint_service import _po_clarification_retry_prompt_block


def test_po_clarification_retry_block_includes_prior_detail():
    state.SHARED_BOARD.setdefault("Needs PO", [])
    task = init_new_task({"id": "T-1", "title": "Meal backup", "description": "d", "status": "Needs PO"})
    state.SHARED_BOARD["Needs PO"] = [task]
    record_task_decision(
        "T-1",
        "Product Owner",
        "clarification_incomplete",
        "Develop backup…",
        "Develop the backup functionality for meal data. Follow these steps:\n1. Export\n",
    )
    block = _po_clarification_retry_prompt_block(task)
    assert "PRIOR PO CLARIFICATION" in block
    assert "Follow these steps" in block
    assert "JSON" in block


def test_po_llm_skip_block_reason_missing_ac():
    from backend.services.po_clarification import po_llm_skip_block_reason

    assert po_llm_skip_block_reason({"description": "x", "acceptanceCriteria": []}) == (
        "missing_acceptance_criteria"
    )
    assert (
        po_llm_skip_block_reason(
            {
                "description": "x",
                "acceptanceCriteria": ["y"],
                "lastStepOutcome": {"exitReason": "read_only_no_edits"},
            }
        )
        == ""
    )
    from backend.services.po_clarification import should_move_off_needs_po_without_llm

    assert should_move_off_needs_po_without_llm(
        {
            "description": "x",
            "acceptanceCriteria": ["y"],
            "lastStepOutcome": {"exitReason": "read_only_no_edits"},
        }
    )

    task = {
        "description": "Delete stores via repository",
        "acceptanceCriteria": ["deleting a store removes it"],
        "lastStepOutcome": {"exitReason": "max_iterations_after_writes"},
    }
    assert should_move_off_needs_po_without_llm(task) is True
    assert should_move_off_needs_po_without_llm({"description": "x"}) is False
    spec = {
        "description": "Lint remaining overflow files",
        "acceptanceCriteria": ["analyze is clean"],
    }
    for reason in (
        "max_iterations",
        "po_clarification_incomplete",
        "completed_text_only",
        "empty_generation_timeout",
        "llm_call_failed",
        "interrupted",
    ):
        assert should_move_off_needs_po_without_llm(
            {**spec, "lastStepOutcome": {"exitReason": reason}}
        ) is True


def test_run_po_clarification_skips_llm_when_spec_present():
    from unittest.mock import patch

    from backend.agents.task_context import get_task_lane
    from backend.bootstrap import initialize
    from backend.services.sprint_service import _run_po_clarification
    from backend.services.workflow_settings import reset_workflow_settings

    initialize()
    reset_workflow_settings()
    task = init_new_task(
        {
            "id": "T-SKIP-PO",
            "title": "Delete tests",
            "description": "Implement delete store tests",
            "acceptanceCriteria": ["delete removes the store"],
            "status": "Needs PO",
        }
    )
    task["lastStepOutcome"] = {"exitReason": "max_iterations_after_writes", "agent": "Developer"}
    state.SHARED_BOARD.setdefault("Needs PO", [])
    state.SHARED_BOARD["Needs PO"] = [task]
    with patch("backend.services.sprint_service.agent_po.execute_step") as execute, patch(
        "backend.services.sprint_service._run_developer_step"
    ):
        _run_po_clarification(task, "brief")
    execute.assert_not_called()
    assert get_task_lane("T-SKIP-PO") == "In Progress"
    events = (state.LAST_STEP_DIAGNOSTICS or {}).get("events") or []
    assert any(e.get("kind") == "po_llm_skipped" for e in events)


def test_run_po_clarification_logs_started_when_skip_blocked():
    from unittest.mock import patch

    from backend.bootstrap import initialize
    from backend.services.sprint_service import _run_po_clarification
    from backend.services.step_diagnostics import clear_active_step_trace
    from backend.services.workflow_settings import reset_workflow_settings

    initialize()
    reset_workflow_settings()
    clear_active_step_trace()
    task = init_new_task(
        {
            "id": "T-PO-START",
            "title": "Needs AC",
            "description": "Has a description",
            "acceptanceCriteria": [],
            "status": "Needs PO",
        }
    )
    state.SHARED_BOARD.setdefault("Needs PO", [])
    state.SHARED_BOARD["Needs PO"] = [task]
    with patch(
        "backend.services.sprint_service.agent_po.execute_step",
        return_value="clarifying",
    ) as execute:
        _run_po_clarification(task, "brief")
    execute.assert_called_once()
    events = (state.LAST_STEP_DIAGNOSTICS or {}).get("events") or []
    kinds = [e.get("kind") for e in events]
    assert "po_llm_started" in kinds
    assert "po_llm_skipped" not in kinds
    assert any("missing_acceptance_criteria" in str(e.get("message")) for e in events)


def test_run_po_clarification_skips_llm_after_max_iterations():
    from unittest.mock import patch

    from backend.agents.task_context import get_task_lane
    from backend.bootstrap import initialize
    from backend.services.sprint_service import _run_po_clarification
    from backend.services.workflow_settings import reset_workflow_settings

    initialize()
    reset_workflow_settings()
    task = init_new_task(
        {
            "id": "T-SKIP-MAXITER",
            "title": "Lint overflow",
            "description": "Fix overflow lint",
            "acceptanceCriteria": ["no overflow warnings"],
            "status": "Needs PO",
        }
    )
    task["lastStepOutcome"] = {"exitReason": "max_iterations", "agent": "Product Owner"}
    state.SHARED_BOARD.setdefault("Needs PO", [])
    state.SHARED_BOARD["Needs PO"] = [task]
    with patch("backend.services.sprint_service.agent_po.execute_step") as execute:
        _run_po_clarification(task, "brief")
    execute.assert_not_called()
    assert get_task_lane("T-SKIP-MAXITER") == "In Progress"


def test_run_po_skip_reapplies_force_patch_after_read_only():
    from unittest.mock import patch

    from backend.agents.task_context import get_task_lane
    from backend.bootstrap import initialize
    from backend.services.sprint_service import _run_po_clarification
    from backend.services.workflow_settings import reset_workflow_settings

    initialize()
    reset_workflow_settings()
    task = init_new_task(
        {
            "id": "T-SKIP-READONLY",
            "title": "Export flow",
            "description": "Implement export",
            "acceptanceCriteria": ["user can export"],
            "status": "Needs PO",
        }
    )
    task["lastStepOutcome"] = {"exitReason": "read_only_no_edits", "agent": "Developer"}
    task["identicalPoClarificationCount"] = 1
    state.SHARED_BOARD.setdefault("Needs PO", [])
    state.SHARED_BOARD["Needs PO"] = [task]
    with patch("backend.services.sprint_service.agent_po.execute_step") as execute, patch(
        "backend.services.sprint_service._run_developer_step"
    ):
        _run_po_clarification(task, "brief")
    execute.assert_not_called()
    assert get_task_lane("T-SKIP-READONLY") == "In Progress"
    live = state.SHARED_BOARD.get("In Progress", [])
    moved = next((t for t in live if t.get("id") == "T-SKIP-READONLY"), None)
    assert moved is not None
    assert moved.get("forcePatchNextDevStep") is True
