"""Claim-ready backlog API and move with skip refinement."""

from backend.bootstrap import initialize


def test_claim_ready_moves_refinement_complete_backlog():
    initialize()
    from backend import state
    from backend.agents.task_context import init_new_task
    from backend.services.workflow_settings import save_workflow_settings

    save_workflow_settings({"requireBacklogRefinement": True})

    ready = init_new_task(
        {
            "id": "T-READY",
            "title": "Ready feature",
            "description": "Build it",
            "status": "Backlog",
            "refinementComplete": True,
        }
    )
    not_ready = init_new_task(
        {
            "id": "T-NOT",
            "title": "Needs refinement",
            "description": "Spike first",
            "status": "Backlog",
            "refinementComplete": False,
        }
    )
    state.SHARED_BOARD = {"Backlog": [not_ready, ready]}

    from backend.main import app
    from fastapi.testclient import TestClient

    client = TestClient(app)
    resp = client.post("/api/board/claim-ready", json={"limit": 5})
    assert resp.status_code == 200
    body = resp.json()
    assert "T-READY" in body["claimedTaskIds"]
    assert "T-NOT" not in body["claimedTaskIds"]
    board = body["board"]
    in_progress_ids = [t["id"] for t in board.get("In Progress", [])]
    assert "T-READY" in in_progress_ids


def test_move_with_skip_refinement_sets_flag():
    initialize()
    from backend import state
    from backend.agents.task_context import init_new_task, find_task_by_id

    task = init_new_task(
        {
            "id": "T-SKIP",
            "title": "Skip refine",
            "description": "d",
            "status": "Refinement",
            "refinementComplete": False,
        }
    )
    state.SHARED_BOARD = {"Refinement": [task]}

    from backend.main import app
    from fastapi.testclient import TestClient

    client = TestClient(app)
    resp = client.post(
        "/api/tasks/move",
        json={
            "taskId": "T-SKIP",
            "fromLane": "Refinement",
            "toLane": "In Progress",
            "skipRefinement": True,
        },
    )
    assert resp.status_code == 200
    saved = find_task_by_id("T-SKIP")
    assert saved is not None
    assert saved.get("refinementComplete") is True
    assert saved.get("status") == "In Progress"
