"""Meta-refusal classification, exit reason, and triage."""

from backend.services.dev_refusal_triage import (
    classify_dev_text_refusal,
    diagnose_dev_refusal,
    fast_stop_refusal_class,
)
from backend.services.step_diagnostics import derive_exit_reason


def test_classify_meta_refusal():
    msg = "I can't complete this task because the system cannot proceed with the required operations."
    assert classify_dev_text_refusal(msg) == "meta_refusal"
    assert fast_stop_refusal_class("meta_refusal") is True


def test_classify_unable_to_proceed_from_traces():
    msg = (
        "I'm unable to proceed with the task as described due to repeated failures "
        "in applying patches. The current approach is blocked."
    )
    assert classify_dev_text_refusal(msg) == "meta_refusal"


def test_classify_safety_still_works():
    assert classify_dev_text_refusal("I'm sorry, but I can't assist with that.") == "safety_refusal"


def test_derive_exit_reason_meta_refusal():
    reason = derive_exit_reason(
        agent_result="Stopped: model meta refusal — card deferred for sprint triage.",
        tools_used=set(),
        lane_before="In Progress",
        lane_after="In Progress",
    )
    assert reason == "meta_refusal"


def test_diagnose_tool_policy_deadlock():
    task = {
        "id": "T1",
        "title": "Export",
        "description": "d",
        "scope": "s",
        "testPlan": "t",
        "transcript": [
            {"toolOutput": "Error: write_file blocked — 'lib/x.dart' already exists."},
        ],
    }
    diag = diagnose_dev_refusal(
        task,
        exit_reason="meta_refusal",
        agent_result="cannot proceed with the required operations",
    )
    assert diag["cause"] == "tool_policy_deadlock"
    assert diag["toolPolicyDeadlock"] is True
    assert diag["recommendedAction"] == "defer_pick_next_card"
