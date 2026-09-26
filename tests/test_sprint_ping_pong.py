"""Ping-pong detection and Dev scheduling deprioritize."""

from backend import state
from backend.bootstrap import initialize
from backend.services.sprint_ping_pong import (
    PING_PONG_DEPRIORITIZE_THRESHOLD,
    analyze_task_moves,
    refresh_ping_pong_from_report,
    sort_dev_runnable,
)
from backend.services.sprint_report import begin_sprint_report, record_lane_move


def _moves_ip_nu_round_trips(n: int):
    moves = []
    for i in range(n):
        base = i * 2
        moves.append(
            {
                "taskId": "t1",
                "title": "Churn",
                "fromLane": "In Progress",
                "toLane": "Needs User",
                "at": f"2026-01-01 10:00:{base:02d}",
            }
        )
        moves.append(
            {
                "taskId": "t1",
                "title": "Churn",
                "fromLane": "Needs User",
                "toLane": "In Progress",
                "at": f"2026-01-01 10:00:{base + 1:02d}",
            }
        )
    return moves


def test_analyze_task_moves_counts_round_trips(tmp_path, monkeypatch):
    monkeypatch.setenv("ALLHANDS_HOME", str(tmp_path))
    initialize()
    stats = analyze_task_moves(_moves_ip_nu_round_trips(2))
    assert stats["pingPongRoundTrips"] == 2
    assert stats["pingPongFlag"] is True
    assert stats["byTransition"]["In Progress → Needs User"] == 2
    assert stats["byTransition"]["Needs User → In Progress"] == 2


def test_refresh_ping_pong_sets_deprioritize_on_board(tmp_path, monkeypatch):
    monkeypatch.setenv("ALLHANDS_HOME", str(tmp_path))
    initialize()
    state.SHARED_BOARD = {
        "In Progress": [{"id": "t1", "title": "Churn", "priority": 1}],
        "Needs User": [],
    }
    state.SPRINT_PROGRESS_STEP = 5
    begin_sprint_report()
    for move in _moves_ip_nu_round_trips(PING_PONG_DEPRIORITIZE_THRESHOLD):
        record_lane_move(
            move["taskId"],
            move["title"],
            move["fromLane"],
            move["toLane"],
        )

    refresh_ping_pong_from_report()
    task = state.SHARED_BOARD["In Progress"][0]
    assert task.get("pingPongRoundTrips") == PING_PONG_DEPRIORITIZE_THRESHOLD
    assert int(task.get("pingPongDeprioritizedUntilStep") or 0) == 5 + 8


def test_sort_dev_runnable_puts_ping_pong_last():
    low = {"id": "a", "priority": 50}
    high_pri = {"id": "b", "priority": 1, "pingPongDeprioritizedUntilStep": 999}
    ordered = sort_dev_runnable([high_pri, low])
    assert ordered[0]["id"] == "a"
    assert ordered[1]["id"] == "b"


def test_dev_deferred_included_and_sorted_last(tmp_path, monkeypatch):
    monkeypatch.setenv("ALLHANDS_HOME", str(tmp_path))
    initialize()
    state.SPRINT_PROGRESS_STEP = 10
    state.SHARED_BOARD = {
        "In Progress": [
            {"id": "a", "title": "Normal", "priority": 50},
            {"id": "b", "title": "Deferred", "priority": 1, "devDeferredUntilStep": 20},
        ],
        "Needs User": [],
    }
    from backend.services.sprint_service import _sorted_in_progress_dev_runnable

    ordered = _sorted_in_progress_dev_runnable()
    assert [t["id"] for t in ordered] == ["a", "b"]

    state.SHARED_BOARD = {
        "In Progress": [
            {"id": "b", "title": "Deferred", "priority": 1, "devDeferredUntilStep": 20},
        ],
        "Needs User": [],
    }
    sole = _sorted_in_progress_dev_runnable()
    assert len(sole) == 1
    assert sole[0]["id"] == "b"
