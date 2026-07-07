"""Needs User dedup and user resolution persistence tests."""

from backend.bootstrap import initialize
from backend.services.needs_user_guard import (
    append_user_resolution,
    dev_explicit_needs_user,
    question_similarity,
    set_needs_user_cooldown,
    should_escalate_to_needs_user,
)


def test_dev_explicit_needs_user_requires_marker():
    assert dev_explicit_needs_user("Moving to Needs User with userQuestion: API key needed")
    assert not dev_explicit_needs_user("I need an api key in the config file")
    assert not dev_explicit_needs_user("Completed lint fixes")


def test_similar_question_blocked():
    initialize()
    from backend.agents.task_context import init_new_task, normalize_task

    task = init_new_task({"id": "T-DEDUP", "title": "T", "description": "D"})
    append_user_resolution(task, "Which database should we use?", "PostgreSQL", "In Progress")
    normalize_task(task)
    allowed, reason = should_escalate_to_needs_user(task, "Which database should we use?")
    assert allowed is False
    assert reason == "duplicate_question"
    assert task.get("needsUserDuplicate") is True


def test_question_similarity_threshold():
    a = "Please clarify requirements for auth flow"
    b = "Please clarify requirements for the auth flow"
    assert question_similarity(a, b) >= 0.85


def test_resolve_appends_user_resolution():
    initialize()
    from backend import state
    from backend.agents.task_context import init_new_task, normalize_task
    from backend.main import app
    from fastapi.testclient import TestClient

    task = init_new_task(
        {
            "id": "T-RES",
            "title": "Auth",
            "description": "Add login",
            "status": "Needs User",
            "userQuestion": "Which OAuth provider?",
            "needsUserReason": "Which OAuth provider?",
        }
    )
    state.SHARED_BOARD = {
        "Backlog": [],
        "In Progress": [],
        "Needs PO": [],
        "Needs User": [task],
        "QA": [],
        "Done": [],
    }
    client = TestClient(app)
    resp = client.post(
        "/api/tasks/T-RES/resolve-user",
        json={"answer": "Use Google OAuth", "target": "dev"},
    )
    assert resp.status_code == 200
    updated = next(t for t in resp.json()["board"]["In Progress"] if t["id"] == "T-RES")
    normalize_task(updated)
    assert len(updated.get("userResolutions") or []) == 1
    assert updated["userResolutions"][0]["answer"] == "Use Google OAuth"
    assert updated.get("needsUserCooldownUntilStep") is not None


def test_cooldown_blocks_re_escalation():
    initialize()
    from backend import state
    from backend.agents.task_context import init_new_task, normalize_task

    state.SPRINT_PROGRESS_STEP = 5
    task = init_new_task({"id": "T-COOL", "title": "T", "description": "D"})
    set_needs_user_cooldown(task, steps=3)
    normalize_task(task)
    assert task["needsUserCooldownUntilStep"] == 8
    allowed, reason = should_escalate_to_needs_user(task, "Need production API key")
    assert allowed is False
    assert reason == "cooldown_active"
