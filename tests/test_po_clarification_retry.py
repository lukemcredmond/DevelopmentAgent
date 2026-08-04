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
