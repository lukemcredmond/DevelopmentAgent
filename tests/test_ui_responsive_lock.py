"""STATE_LOCK must not span long agent/Ollama work so secondary APIs stay responsive."""

import threading
from unittest.mock import patch

from backend import state
from backend.agents.task_context import init_new_task
from backend.api.schemas import BriefPayload
from backend.api.sprint import cancel_auto_sprint, trigger_po_plan_outline
from backend.bootstrap import initialize
from backend.services.sprint_service import run_po_plan_outline, run_sprint_step


def test_lock_free_during_plan_outline_execute_step():
    initialize()
    acquired = threading.Event()
    release_llm = threading.Event()

    def slow_execute(*_a, **_k):
        # While "Ollama" runs, another thread must be able to take STATE_LOCK.
        got = state.STATE_LOCK.acquire(timeout=1.0)
        assert got, "could not acquire STATE_LOCK during execute_step"
        acquired.set()
        state.STATE_LOCK.release()
        release_llm.wait(timeout=2.0)
        return "## Summary\nResponsive plan\n"

    with patch("backend.services.sprint_service.agent_po") as mock_po:
        mock_po.execute_step.side_effect = slow_execute
        worker = threading.Thread(
            target=lambda: run_po_plan_outline("Build app", "http://localhost:11434"),
            daemon=True,
        )
        worker.start()
        assert acquired.wait(timeout=3.0), "STATE_LOCK was held across Ollama-like work"
        release_llm.set()
        worker.join(timeout=5.0)
        assert not worker.is_alive()


def test_plan_outline_route_does_not_wrap_ollama_in_lock():
    """API handler must not hold STATE_LOCK around run_po_plan_outline."""
    initialize()
    acquired = threading.Event()
    release_llm = threading.Event()

    def slow_execute(*_a, **_k):
        got = state.STATE_LOCK.acquire(timeout=1.0)
        if got:
            acquired.set()
            state.STATE_LOCK.release()
        release_llm.wait(timeout=2.0)
        return "## Summary\nVia API\n"

    with patch("backend.services.sprint_service.agent_po") as mock_po:
        mock_po.execute_step.side_effect = slow_execute
        worker = threading.Thread(
            target=lambda: trigger_po_plan_outline(
                BriefPayload(brief="Build app", ollama_url="http://localhost:11434")
            ),
            daemon=True,
        )
        worker.start()
        assert acquired.wait(timeout=3.0), "STATE_LOCK was held by /api/plan/outline"
        release_llm.set()
        worker.join(timeout=5.0)
        assert not worker.is_alive()


def test_cancel_sprint_works_while_state_lock_held():
    """Cancel must stay fast even if another request holds STATE_LOCK."""
    initialize()
    state.SPRINT_CANCEL = False
    release_holder = threading.Event()
    holder_ready = threading.Event()

    def hold_lock():
        with state.STATE_LOCK:
            holder_ready.set()
            release_holder.wait(timeout=5.0)

    holder = threading.Thread(target=hold_lock, daemon=True)
    holder.start()
    assert holder_ready.wait(timeout=2.0)

    result = cancel_auto_sprint()
    assert result["ok"] is True
    assert state.SPRINT_CANCEL is True

    release_holder.set()
    holder.join(timeout=2.0)


def test_lock_free_during_developer_step():
    initialize()
    state.SHARED_BOARD.clear()
    for lane in (
        "Backlog",
        "In Progress",
        "Needs User",
        "Needs PO",
        "QA",
        "Done",
        "Refinement",
        "Code Review",
    ):
        state.SHARED_BOARD[lane] = []
    task = init_new_task(
        {"id": "T-1", "title": "Work", "description": "Do it", "status": "In Progress"}
    )
    state.SHARED_BOARD["In Progress"] = [task]

    acquired = threading.Event()
    release_llm = threading.Event()

    def slow_dev(*_a, **_k):
        got = state.STATE_LOCK.acquire(timeout=1.0)
        assert got, "could not acquire STATE_LOCK during developer step"
        acquired.set()
        state.STATE_LOCK.release()
        release_llm.wait(timeout=2.0)

    with patch(
        "backend.services.sprint_service._run_developer_step",
        side_effect=slow_dev,
    ):
        worker = threading.Thread(
            target=lambda: run_sprint_step("brief", "http://localhost:11434"),
            daemon=True,
        )
        worker.start()
        assert acquired.wait(timeout=3.0), "STATE_LOCK held during developer step"
        release_llm.set()
        worker.join(timeout=5.0)
        assert not worker.is_alive()
