import json
import queue
from typing import Any, Dict, Generator, List

from backend import state


def publish_event(event_type: str, data: Dict[str, Any]) -> None:
    """Broadcasts an event to all SSE subscribers."""
    payload = {"type": event_type, "data": data}
    dead: List[Any] = []
    for subscriber in state.EVENT_SUBSCRIBERS:
        try:
            subscriber.put_nowait(payload)
        except Exception:
            dead.append(subscriber)
    for sub in dead:
        if sub in state.EVENT_SUBSCRIBERS:
            state.EVENT_SUBSCRIBERS.remove(sub)


def event_stream() -> Generator[str, None, None]:
    """SSE generator yielding JSON events."""
    q: queue.Queue = queue.Queue()
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
