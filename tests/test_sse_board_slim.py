"""SSE queue capping and board payload slimming."""

import queue
from unittest.mock import patch

from backend import state
from backend.bootstrap import initialize
from backend.services.board_service import publish_board_delta, publish_board_update
from backend.services.events import publish_event, slim_board_for_sse


def test_sse_queue_drops_oldest_when_full():
    q: queue.Queue = queue.Queue(maxsize=2)
    state.EVENT_SUBSCRIBERS = [q]
    try:
        publish_event("a", {"n": 1})
        publish_event("a", {"n": 2})
        publish_event("a", {"n": 3})  # should drop 1
        items = []
        while not q.empty():
            items.append(q.get_nowait())
        assert len(items) == 2
        assert items[0]["data"]["n"] == 2
        assert items[1]["data"]["n"] == 3
    finally:
        state.EVENT_SUBSCRIBERS = []


def test_slim_board_truncates_transcript():
    board = {
        "Backlog": [
            {
                "id": "T1",
                "title": "x",
                "transcript": [{"i": n} for n in range(20)],
                "decisions": [{"i": n} for n in range(20)],
            }
        ]
    }
    slim = slim_board_for_sse(board)
    assert len(slim["Backlog"][0]["transcript"]) == 5
    assert slim["Backlog"][0].get("transcriptTruncated") is True
    assert len(slim["Backlog"][0]["decisions"]) == 8


def test_publish_board_update_uses_slim_coalesce():
    initialize()
    captured = []

    def capture(data):
        captured.append(data)

    with patch("backend.services.events.publish_board_event_coalesced", side_effect=capture):
        state.SHARED_BOARD["Backlog"] = [
            {
                "id": "T1",
                "title": "x",
                "transcript": [{"i": n} for n in range(12)],
            }
        ]
        publish_board_update(source="test")
    assert captured
    assert len(captured[0]["board"]["Backlog"][0]["transcript"]) == 5


def test_publish_board_delta_slims_task():
    initialize()
    events = []

    def capture(etype, data):
        events.append((etype, data))

    state.SHARED_BOARD["In Progress"] = [
        {
            "id": "T2",
            "title": "y",
            "status": "In Progress",
            "transcript": [{"i": n} for n in range(10)],
        }
    ]
    with patch("backend.services.events.publish_event", side_effect=capture):
        publish_board_delta("T2", source="test")
    assert events
    assert events[0][0] == "board"
    assert events[0][1]["delta"] is True
    assert len(events[0][1]["task"]["transcript"]) == 5
