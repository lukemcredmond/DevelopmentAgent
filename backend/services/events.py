import json
import queue
import threading
from typing import Any, Dict, Generator, List, Optional

from backend import state

# Cap per-subscriber backlog so a slow UI cannot balloon memory.
SSE_QUEUE_MAXSIZE = 256

# Coalesce rapid full board snapshots (last-wins).
_BOARD_COALESCE_MS = 250
_board_timer: Optional[threading.Timer] = None
_board_pending: Optional[Dict[str, Any]] = None
_board_lock = threading.Lock()


def publish_event(event_type: str, data: Dict[str, Any]) -> None:
    """Broadcasts an event to all SSE subscribers (drops oldest on overflow)."""
    payload = {"type": event_type, "data": data}
    dead: List[Any] = []
    for subscriber in list(state.EVENT_SUBSCRIBERS):
        try:
            subscriber.put_nowait(payload)
        except queue.Full:
            try:
                subscriber.get_nowait()
            except queue.Empty:
                pass
            try:
                subscriber.put_nowait(payload)
            except queue.Full:
                dead.append(subscriber)
        except Exception:
            dead.append(subscriber)
    for sub in dead:
        if sub in state.EVENT_SUBSCRIBERS:
            state.EVENT_SUBSCRIBERS.remove(sub)


def event_stream() -> Generator[str, None, None]:
    """SSE generator yielding JSON events."""
    q: queue.Queue = queue.Queue(maxsize=SSE_QUEUE_MAXSIZE)
    state.EVENT_SUBSCRIBERS.append(q)
    try:
        yield "data: {\"type\":\"connected\"}\n\n"
        while True:
            try:
                event = q.get(timeout=30)
                yield f"data: {json.dumps(event)}\n\n"
            except queue.Empty:
                yield ": keepalive\n\n"
    finally:
        if q in state.EVENT_SUBSCRIBERS:
            state.EVENT_SUBSCRIBERS.remove(q)


def _slim_task_for_sse(task: Dict[str, Any]) -> Dict[str, Any]:
    """Omit heavy transcript/decision tails from board SSE payloads."""
    slim = dict(task)
    transcript = slim.get("transcript")
    if isinstance(transcript, list) and len(transcript) > 5:
        slim["transcript"] = transcript[-5:]
        slim["transcriptTruncated"] = True
    decisions = slim.get("decisions")
    if isinstance(decisions, list) and len(decisions) > 8:
        slim["decisions"] = decisions[-8:]
        slim["decisionsTruncated"] = True
    return slim


def slim_board_for_sse(board: Dict[str, List[Dict[str, Any]]]) -> Dict[str, List[Dict[str, Any]]]:
    return {
        lane: [_slim_task_for_sse(t) if isinstance(t, dict) else t for t in (tasks or [])]
        for lane, tasks in board.items()
    }


def _flush_coalesced_board() -> None:
    global _board_timer, _board_pending
    with _board_lock:
        pending = _board_pending
        _board_pending = None
        _board_timer = None
    if pending is not None:
        publish_event("board", pending)


def publish_board_event_coalesced(data: Dict[str, Any]) -> None:
    """Last-wins debounce for full board snapshots during bursty sprint updates."""
    global _board_timer, _board_pending
    with _board_lock:
        _board_pending = data
        if _board_timer is not None:
            _board_timer.cancel()
        _board_timer = threading.Timer(_BOARD_COALESCE_MS / 1000.0, _flush_coalesced_board)
        _board_timer.daemon = True
        _board_timer.start()
