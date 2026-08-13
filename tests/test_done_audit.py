"""Done column audit — incomplete dev/QA evidence on Done cards."""

from __future__ import annotations

from backend.agents.task_context import init_new_task, normalize_task
from backend.bootstrap import initialize
from backend import state
from backend.services.done_audit import apply_done_audit_actions, audit_done_tasks, audit_single_done_task


def test_audit_flags_done_without_write_evidence():
    initialize()
    state.SHARED_BOARD.clear()
    task = init_new_task({"id": "T-INC", "title": "Incomplete", "description": "d", "status": "Done"})
    state.SHARED_BOARD["Done"] = [task]
    row = audit_single_done_task(task)
    assert row is not None
    assert row["taskId"] == "T-INC"
    assert any("Agent progress pending" in r for r in row["reasons"])


def test_audit_clear_after_write_transcript():
    initialize()
    state.SHARED_BOARD.clear()
    task = init_new_task({"id": "T-OK", "title": "OK", "description": "d", "status": "Done"})
    task["files"] = [{"path": "a.py", "action": "written"}]
    task["transcript"] = [
        {"toolName": "read_file", "toolSuccess": True},
        {"toolName": "write_file", "toolSuccess": True},
        {"toolName": "run_command", "toolSuccess": True, "toolArgs": {"command": "npm test"}},
    ]
    normalize_task(task)
    state.SHARED_BOARD["Done"] = [task]
    row = audit_single_done_task(task)
    assert row is None


def test_apply_routes_incomplete_spec_to_needs_po_instead_of_bypassing_dev_gate():
    initialize()
    state.SHARED_BOARD.clear()
    task = init_new_task({"id": "T-MV", "title": "Move me", "description": "d", "status": "Done"})
    state.SHARED_BOARD["Done"] = [task]
    result = apply_done_audit_actions(["T-MV"], "In Progress", only_incomplete=True)
    assert "T-MV" in result["moved"]
    assert any(t.get("id") == "T-MV" for t in state.SHARED_BOARD.get("Needs PO") or [])


def test_audit_report_counts():
    initialize()
    state.SHARED_BOARD.clear()
    t1 = init_new_task({"id": "T-A", "title": "a", "description": "d", "status": "Done"})
    t2 = init_new_task({"id": "T-B", "title": "b", "description": "d", "status": "Done"})
    t2["files"] = [{"path": "x.py", "action": "read"}, {"path": "x.py", "action": "written"}]
    t2["transcript"] = [
        {"toolName": "read_file", "toolSuccess": True},
        {"toolName": "write_file", "toolSuccess": True},
        {"toolName": "run_command", "toolSuccess": True},
    ]
    normalize_task(t2)
    state.SHARED_BOARD["Done"] = [t1, t2]
    report = audit_done_tasks()
    assert report["totalDone"] == 2
    assert report["incompleteCount"] == 1
    assert report["items"][0]["taskId"] == "T-A"
