"""Card delivery and SDD inherit tests."""

from __future__ import annotations

from unittest.mock import patch

from backend import state
from backend.agents.task_context import init_new_task, normalize_task
from backend.bootstrap import initialize
from backend.services.board_service import append_backlog_tasks
from backend.services.card_delivery import (
    build_expected_summary,
    sync_ac_verification,
    sync_card_delivery_fields,
    update_ac_verification_from_qa,
)
from backend.services.task_sdd_inherit import apply_feature_sdd_defaults
from backend.services.task_spec_markdown import build_task_spec_markdown


def _clear_board():
    state.SHARED_BOARD.clear()
    for lane in (
        "Features",
        "Backlog",
        "Pending Approval",
        "Refinement",
        "In Progress",
        "Needs User",
        "Needs PO",
        "Code Review",
        "QA",
        "Done",
    ):
        state.SHARED_BOARD[lane] = []


def test_sync_ac_verification_preserves_actual_on_unchanged_criterion():
    task = {
        "id": "T1",
        "acceptanceCriteria": ["Login works", "Logout works"],
        "acChecklist": [True, False],
        "acVerification": [
            {
                "criterion": "Login works",
                "expected": "Login works",
                "actual": "Manual sign-in OK",
                "met": True,
                "updatedAt": "t1",
            }
        ],
    }
    normalize_task(task)
    sync_ac_verification(task)
    rows = task["acVerification"]
    assert len(rows) == 2
    assert rows[0]["actual"] == "Manual sign-in OK"
    assert rows[0]["met"] is True
    assert rows[1]["criterion"] == "Logout works"


def test_qa_update_sets_actual_summary():
    task = init_new_task(
        {
            "id": "T2",
            "title": "Auth",
            "description": "OAuth",
            "acceptanceCriteria": ["A", "B"],
            "status": "QA",
        }
    )
    sync_card_delivery_fields(task)
    update_ac_verification_from_qa(
        task, passed=True, commands=["npm test"], failure_reason=""
    )
    assert "Playbook: passed" in (task.get("actualSummary") or "")
    assert task["acVerification"][0]["met"] is True


def test_spec_markdown_includes_expected_and_actual_sections():
    task = init_new_task(
        {
            "id": "T3",
            "title": "Slice",
            "description": "Do thing",
            "acceptanceCriteria": ["Done"],
            "status": "Backlog",
        }
    )
    sync_card_delivery_fields(task)
    task["actualSummary"] = "Shipped feature X"
    md = build_task_spec_markdown(task)
    assert "## Expected result" in md
    assert "## Actual result" in md
    assert "## Acceptance verification" in md
    assert "Shipped feature X" in md


def test_apply_feature_sdd_defaults_user_story_and_scope():
    feature = init_new_task(
        {
            "id": "FEAT-1",
            "title": "Meal planning",
            "description": "Users plan weekly meals. Saves time.",
            "workType": "feature",
            "status": "Features",
        }
    )
    state.SHARED_BOARD.setdefault("Features", []).append(feature)
    child = {
        "id": "CH-1",
        "featureId": "FEAT-1",
        "title": "Week view",
        "description": "Show 7-day grid",
        "acceptanceCriteria": ["Grid renders"],
    }
    inherited = apply_feature_sdd_defaults(child, feature)
    assert "userStory" in inherited
    assert "scope" in inherited
    assert "Week view" in child["scope"]
    assert "Meal planning" in child["userStory"]


def test_run_auto_sprint_session_refresh_status():
    initialize()
    _clear_board()
    task = init_new_task(
        {
            "id": "RUN-1",
            "title": "Work",
            "description": "d",
            "acceptanceCriteria": ["a", "b"],
            "status": "Backlog",
        }
    )
    state.SHARED_BOARD["Backlog"] = [task]

    from backend.services import sprint_service

    with patch.object(sprint_service, "run_sprint_step", return_value=None):
        with patch.object(sprint_service, "has_sprint_work", return_value=True):
            with patch(
                "backend.services.simulation_gate.has_pending_simulation", return_value=False
            ):
                with patch("time.monotonic", side_effect=[0.0, 4000.0]):
                    summary = sprint_service.run_auto_sprint(
                        "brief", "http://localhost:11434", max_steps=10
                    )
    assert summary.get("status") == "session_refresh"
