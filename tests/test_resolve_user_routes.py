"""Needs User resolve routing tests."""

from backend import state
from backend.agents.task_context import init_new_task
from backend.bootstrap import initialize
from backend.services.board_service import move_board_stage


def _setup_needs_user_task(task_id: str = "T-RESOLVE"):
    for lane in (
        "Backlog",
        "In Progress",
        "Needs User",
        "Needs PO",
        "Refinement",
        "QA",
        "Done",
        "Code Review",
        "Pending Approval",
    ):
        state.SHARED_BOARD.setdefault(lane, [])
    task = init_new_task(
        {
            "id": task_id,
            "title": "Blocked",
            "description": "Need input",
            "status": "Needs User",
            "needsUserReason": "Missing API key",
            "needsUserAction": "Provide key",
        }
    )
    state.SHARED_BOARD["Needs User"] = [task]
    return task


def test_resolve_user_to_refinement():
    initialize()
    from fastapi.testclient import TestClient
    from backend.main import app

    _setup_needs_user_task("T-REF")
    client = TestClient(app)
    resp = client.post(
        "/api/tasks/T-REF/resolve-user",
        json={"answer": "Use staging credentials", "target": "refinement"},
    )
    assert resp.status_code == 200
    board = resp.json()["board"]
    assert any(t["id"] == "T-REF" for t in board.get("Refinement", []))
    assert not any(t["id"] == "T-REF" for t in board.get("Needs User", []))


def test_resolve_user_to_po():
    initialize()
    from fastapi.testclient import TestClient
    from backend.main import app

    _setup_needs_user_task("T-PO")
    client = TestClient(app)
    resp = client.post(
        "/api/tasks/T-PO/resolve-user",
        json={"answer": "Clarify scope with PO", "target": "po"},
    )
    assert resp.status_code == 200
    board = resp.json()["board"]
    assert any(t["id"] == "T-PO" for t in board.get("Needs PO", []))


def test_reset_dev_latch_route():
    initialize()
    from fastapi.testclient import TestClient
    from backend.main import app

    task = _setup_needs_user_task("T-LATCH")
    task["phaseCycleCapReached"] = True
    task["devStepCount"] = 8
    task["needsUserKind"] = "phase_cycle_cap"
    client = TestClient(app)
    resp = client.post("/api/tasks/T-LATCH/reset-dev-latch")
    assert resp.status_code == 200
    body = resp.json()
    board = body["board"]
    live = next(t for t in board.get("In Progress", []) if t["id"] == "T-LATCH")
    assert live.get("phaseCycleCapReached") is False
    assert live.get("devStepCount") == 0
    assert body.get("resetLatchWarning")


def test_resolve_user_reset_latch_clears_cap():
    initialize()
    from fastapi.testclient import TestClient
    from backend.main import app

    task = _setup_needs_user_task("T-RESET")
    task["phaseCycleCapReached"] = True
    task["devStepCount"] = 9
    task["needsUserKind"] = "phase_cycle_cap"
    task["needsUserOptions"] = [
        {
            "id": "b",
            "label": "Reset Developer visit latch",
            "answer": "Reset the phase cycle cap and continue implementation on this card.",
            "target": "dev",
        }
    ]
    client = TestClient(app)
    resp = client.post(
        "/api/tasks/T-RESET/resolve-user",
        json={
            "answer": "Reset the phase cycle cap and continue implementation on this card.",
            "target": "dev",
            "optionId": "b",
        },
    )
    assert resp.status_code == 200
    board = resp.json()["board"]
    live = next(t for t in board.get("In Progress", []) if t["id"] == "T-RESET")
    assert live.get("phaseCycleCapReached") is False
    assert live.get("devStepCount") == 0


def test_reset_sprint_stalls_route():
    initialize()
    from fastapi.testclient import TestClient
    from backend.main import app

    for lane in (
        "Backlog",
        "In Progress",
        "Needs User",
        "Needs PO",
        "Refinement",
        "QA",
        "Done",
        "Code Review",
        "Pending Approval",
        "Blocked",
    ):
        state.SHARED_BOARD.setdefault(lane, [])

    ip = init_new_task(
        {
            "id": "T-STALL-IP",
            "title": "Stuck dev",
            "description": "x",
            "status": "In Progress",
            "phaseCycleCapReached": True,
            "poAutoSkip": True,
            "consecutiveBadExits": 5,
            "stuckLoops": 3,
            "devStepCount": 12,
        }
    )
    ip["lastStepProgress"] = {"devPhaseGraph": {"cycle": 13}}
    np = init_new_task(
        {
            "id": "T-STALL-NP",
            "title": "Stuck po",
            "description": "y",
            "status": "Needs PO",
            "poAutoSkip": True,
            "consecutiveBadExits": 4,
            "circuitBreakerPoRetryUsed": True,
            "splitAttemptedOnStuck": True,
        }
    )
    nu = init_new_task(
        {
            "id": "T-STALL-NU",
            "title": "Stuck user",
            "description": "z",
            "status": "Needs User",
            "latchedRecoveryAttempted": True,
            "consecutiveBadExits": 2,
        }
    )
    state.SHARED_BOARD["In Progress"] = [ip]
    state.SHARED_BOARD["Needs PO"] = [np]
    state.SHARED_BOARD["Needs User"] = [nu]

    client = TestClient(app)
    resp = client.post("/api/board/reset-sprint-stalls")
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("resetCount") == 3

    live_ip = next(t for t in body["board"]["In Progress"] if t["id"] == "T-STALL-IP")
    assert live_ip.get("phaseCycleCapReached") is False
    assert live_ip.get("poAutoSkip") is False
    assert live_ip.get("consecutiveBadExits") == 0
    assert live_ip.get("stuckLoops") == 0
    assert live_ip.get("devStepCount") == 0
    assert live_ip["lastStepProgress"]["devPhaseGraph"]["cycle"] == 0

    live_np = next(t for t in body["board"]["Needs PO"] if t["id"] == "T-STALL-NP")
    assert live_np.get("poAutoSkip") is False
    assert live_np.get("circuitBreakerPoRetryUsed") is None
    assert live_np.get("splitAttemptedOnStuck") is None

    live_nu = next(t for t in body["board"]["Needs User"] if t["id"] == "T-STALL-NU")
    assert live_nu.get("latchedRecoveryAttempted") is False
    assert live_nu.get("consecutiveBadExits") == 0
