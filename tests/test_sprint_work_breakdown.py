"""Sprint work breakdown and idle diagnostics."""

from __future__ import annotations

import copy

import pytest

from backend import state
from backend.services import sprint_service as ss


@pytest.fixture
def ws():
    return {
        "requireCodeReview": False,
        "requireBacklogRefinement": False,
        "prioritizeImplementationOverRefinement": True,
        "maxConsecutiveNoWriteStall": 2,
        "enableStuckCircuitBreaker": True,
        "circuitBreakerMaxBadExits": 3,
        "circuitBreakerIdenticalPatchFails": 3,
        "implementerStopOnLatch": True,
    }


def test_summarize_idle_board_with_stalls(monkeypatch, ws):
    board = copy.deepcopy(state.SHARED_BOARD)
    board["In Progress"] = [
        {
            "id": "T-STALL",
            "title": "Stuck card",
            "consecutiveNoWriteStall": 3,
            "latchedRecoveryAttempted": True,
        }
    ]
    board["Needs PO"] = [
        {
            "id": "T-PO",
            "title": "PO circuit",
            "identicalPatchFailCount": 4,
            "circuitBreakerPoRetryUsed": True,
        }
    ]
    monkeypatch.setattr(
        "backend.services.sprint_service.get_workflow_settings", lambda: ws
    )
    with state.STATE_LOCK:
        state.SHARED_BOARD = board
    summary = ss.summarize_sprint_work(board)
    assert summary["hasWork"] is False
    assert summary["totalCards"] >= 2
    assert summary["devRunnable"] == 0
    assert any("no-write" in r.lower() or "circuit" in r.lower() for r in summary["skipReasons"])


def test_summarize_runnable_dev_card(monkeypatch, ws):
    board = copy.deepcopy(state.SHARED_BOARD)
    board["In Progress"] = [
        {
            "id": "T-OK",
            "title": "Runnable",
            "consecutiveNoWriteStall": 0,
        }
    ]
    monkeypatch.setattr(
        "backend.services.sprint_service.get_workflow_settings", lambda: ws
    )
    assert ss.has_sprint_work(board) is True
    summary = ss.summarize_sprint_work(board)
    assert summary["hasWork"] is True
    assert summary["devRunnable"] == 1


def test_meal_planner_unblocked_board_has_work(monkeypatch, ws):
    """Regression: after clearing stall/circuit fields, sprint work exists."""
    board = copy.deepcopy(state.SHARED_BOARD)
    board["In Progress"] = [
        {
            "id": "TASK-CF54589B461A4497AF44FD63FFC712F6",
            "title": "Fix drift dependency in pubspec.yaml",
            "consecutiveNoWriteStall": 0,
            "identicalPatchFailCount": 0,
            "consecutiveBadExits": 0,
            "forcePatchNextDevStep": True,
        }
    ]
    board["Needs PO"] = []
    monkeypatch.setattr(
        "backend.services.sprint_service.get_workflow_settings", lambda: ws
    )
    assert ss.has_sprint_work(board) is True


def test_auto_sprint_runs_step_when_dev_runnable(monkeypatch, ws):
    """After unblock, auto-sprint must not exit idle with stepsRun=0."""
    from unittest.mock import patch

    from backend.services import sprint_service

    board = copy.deepcopy(state.SHARED_BOARD)
    board["In Progress"] = [
        {"id": "T-RUN", "title": "Runnable dev card", "consecutiveNoWriteStall": 0}
    ]
    for lane in list(board.keys()):
        if lane != "In Progress" and isinstance(board.get(lane), list):
            board[lane] = []
    monkeypatch.setattr(
        "backend.services.sprint_service.get_workflow_settings", lambda: ws
    )
    with state.STATE_LOCK:
        state.SHARED_BOARD = board
    steps = []

    def fake_step(brief, ollama_url):
        steps.append(1)
        state.LAST_STEP_OUTCOME = {
            "taskId": "T-RUN",
            "agent": "Developer",
            "exitReason": "lane_advanced",
            "ok": True,
        }

    with patch.object(sprint_service, "run_sprint_step", side_effect=fake_step):
        with patch.object(sprint_service, "publish_sprint_progress"):
            with patch(
                "backend.services.backlog_preflight.log_backlog_preflight_warnings"
            ):
                summary = sprint_service.run_auto_sprint(
                    "brief",
                    "http://localhost:11434",
                    max_steps=3,
                )
    assert len(steps) >= 1
    assert summary.get("stepsRun", 0) >= 1
