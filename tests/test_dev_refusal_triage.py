"""Dev refusal triage: diagnose, defer, and sprint selection skips."""

from backend import state
from backend.agents.task_context import init_new_task
from backend.bootstrap import initialize
from backend.services.dev_refusal_triage import (
    defer_dev_card,
    dev_deferred_active,
    diagnose_dev_refusal,
    maybe_run_dev_refusal_triage,
)
from backend.services.needs_user_guard import clear_needs_user_reason_hash, reason_hash, should_escalate_to_needs_user
from backend.services.sprint_service import _in_progress_dev_runnable


def _board_with_task(task):
    state.SHARED_BOARD.clear()
    for lane in ("Backlog", "In Progress", "Needs User", "Needs PO", "QA", "Done"):
        state.SHARED_BOARD[lane] = []
    state.SHARED_BOARD["In Progress"] = [task]


def test_diagnose_safety_refusal():
    initialize()
    task = init_new_task(
        {
            "id": "T-REF",
            "title": "Initialize Flutter Project",
            "description": "d",
            "scope": "s",
            "testPlan": "t",
            "status": "In Progress",
        }
    )
    diag = diagnose_dev_refusal(
        task,
        exit_reason="safety_refusal",
        agent_result="I'm sorry, but I can't assist with that request.",
    )
    assert diag["cause"] == "model_safety_refusal"
    assert diag["recommendedAction"] == "defer_pick_next_card"


def test_defer_sets_skip_and_clears_hash():
    initialize()
    task = init_new_task(
        {
            "id": "T-DEF",
            "title": "Handle export",
            "description": "d",
            "scope": "s",
            "testPlan": "t",
            "status": "In Progress",
            "lastNeedsUserReasonHash": "abc123",
        }
    )
    _board_with_task(task)
    state.SPRINT_PROGRESS_STEP = 10
    ok = defer_dev_card(
        "T-DEF",
        diagnose_dev_refusal(task, exit_reason="text_rejection_loop"),
    )
    assert ok is True
    live = None
    for lane_tasks in state.SHARED_BOARD.values():
        for t in lane_tasks:
            if str(t.get("id")) == "T-DEF":
                live = t
                break
    assert live is not None
    assert live.get("devDeferredUntilStep") == 15
    assert live.get("lastNeedsUserReasonHash") is None
    assert dev_deferred_active(live) is True
    assert _in_progress_dev_runnable() == []


def test_maybe_triage_on_text_rejection_outcome():
    initialize()
    task = init_new_task(
        {
            "id": "T-TRI",
            "title": "Card",
            "description": "d",
            "scope": "s",
            "testPlan": "t",
            "status": "In Progress",
            "lastStepOutcome": {"exitReason": "text_rejection_loop"},
        }
    )
    _board_with_task(task)
    assert maybe_run_dev_refusal_triage("T-TRI", agent_result="Stopped: text rejection loop") is True


def test_safety_refusal_bypasses_same_reason_hash():
    task = {
        "id": "T-HASH",
        "title": "Feature",
        "lastNeedsUserReasonHash": reason_hash("Model returned text-only (1×) instead of tools."),
    }
    msg = "Model returned text-only (1×) instead of tools on 'Feature' (sprint step 3, ref=deadbeef)."
    allowed, block = should_escalate_to_needs_user(
        task,
        msg,
        kind="stuck_loop",
        safety_refusal=True,
        step_text_rejections=1,
    )
    assert allowed is True
    assert block == ""


def test_clear_needs_user_reason_hash():
    task = {"lastNeedsUserReasonHash": "x", "needsUserDuplicate": True}
    clear_needs_user_reason_hash(task)
    assert task.get("lastNeedsUserReasonHash") is None
    assert task.get("needsUserDuplicate") is False
