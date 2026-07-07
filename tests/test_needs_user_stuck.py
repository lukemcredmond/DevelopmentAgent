"""Stuck-loop escalation tests — prefer PO for lint/tool blockers."""

from backend.bootstrap import initialize


def test_stuck_loop_prefers_po_when_trips_remain():
    initialize()
    from backend import state
    from backend.agents.task_context import init_new_task, get_task_lane
    from backend.services.sprint_service import _check_stuck_and_escalate
    from backend.services.workflow_settings import save_workflow_settings

    save_workflow_settings({"maxStuckSteps": 2, "maxPoRoundTrips": 3})
    task = init_new_task({"id": "T-STUCK", "title": "T", "description": "D", "status": "In Progress"})
    task["stuckLoops"] = 1
    task["lastCommandDiagnostics"] = [
        {"file": "lib/main.dart", "line": 10, "message": "unused import", "severity": "warning"}
    ]
    state.SHARED_BOARD = {
        "Backlog": [],
        "In Progress": [task],
        "Needs PO": [],
        "Needs User": [],
        "QA": [],
        "Done": [],
    }
    _check_stuck_and_escalate("T-STUCK", "In Progress")
    assert get_task_lane("T-STUCK") == "Needs PO"


def test_stuck_loop_skips_needs_user_for_lint_when_po_exhausted():
    initialize()
    from backend import state
    from backend.agents.task_context import init_new_task, get_task_lane
    from backend.services.sprint_service import _check_stuck_and_escalate
    from backend.services.workflow_settings import save_workflow_settings

    save_workflow_settings({"maxStuckSteps": 2, "maxPoRoundTrips": 1})
    task = init_new_task({"id": "T-LINT", "title": "T", "description": "D", "status": "In Progress"})
    task["stuckLoops"] = 1
    task["poRoundTrips"] = 1
    task["lastCommandDiagnostics"] = [
        {"file": "src/app.py", "line": 5, "message": "syntax error", "severity": "error"}
    ]
    state.SHARED_BOARD = {
        "Backlog": [],
        "In Progress": [task],
        "Needs PO": [],
        "Needs User": [],
        "QA": [],
        "Done": [],
    }
    _check_stuck_and_escalate("T-LINT", "In Progress")
    assert get_task_lane("T-LINT") == "In Progress"
    assert len(state.SHARED_BOARD.get("Needs User") or []) == 0
