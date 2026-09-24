"""PO LLM skip churn gate."""

from backend.services.po_clarification import (
    po_skip_churn_should_block,
    should_move_off_needs_po_without_llm,
)


def test_po_skip_churn_blocks_after_two_round_trips():
    task = {
        "title": "Feature",
        "description": "d",
        "acceptanceCriteria": ["ac1"],
        "lastStepOutcome": {"exitReason": "text_rejection_loop"},
        "lastStepProgress": {"cardProgress": {"poRoundTrips": 3}},
    }
    blocked, msg = po_skip_churn_should_block(task)
    assert blocked is True
    assert "text_rejection_loop" in msg
    assert should_move_off_needs_po_without_llm(task) is False


def test_po_skip_churn_allows_first_round_trip():
    task = {
        "title": "Feature",
        "description": "d",
        "acceptanceCriteria": ["ac1"],
        "lastStepOutcome": {"exitReason": "text_rejection_loop"},
        "lastStepProgress": {"cardProgress": {"poRoundTrips": 1}},
    }
    assert po_skip_churn_should_block(task) == (False, "")
