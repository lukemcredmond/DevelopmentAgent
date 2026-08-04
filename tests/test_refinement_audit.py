"""Tests for Refinement lane duplicate audit."""

from backend.agents.task_context import init_new_task, normalize_task
from backend import state
from backend.services.refinement_audit import apply_refinement_audit_actions, audit_refinement_lane


def _task(tid: str, title: str, **extra) -> dict:
    t: dict = {
        "id": tid,
        "title": title,
        "status": "Refinement",
        "description": extra.pop("description", ""),
    }
    t.update(extra)
    return init_new_task(t)


def test_refinement_audit_finds_exact_title_duplicates():
    board = {
        "Refinement": [
            _task("R-1", "Fix login button styling", description="AC1"),
            _task("R-2", "fix login button styling", description=""),
            _task("R-3", "Unrelated card", description="Other work"),
        ],
    }
    report = audit_refinement_lane(board)
    assert report["totalRefinement"] == 3
    assert report["duplicateClusterCount"] == 1
    assert set(report["defaultRemoveTaskIds"]) == {"R-2"}
    cluster = report["clusters"][0]
    assert cluster["suggestedKeepTaskId"] == "R-1"
    assert cluster["matchKind"] == "exact_title"


def test_refinement_audit_quality_flags():
    board = {
        "Refinement": [
            _task("R-4", "x", description=""),
            _task("R-5", "Valid title", refinementComplete=True, description="Done grooming"),
        ],
    }
    report = audit_refinement_lane(board)
    ids = {q["taskId"] for q in report["qualityIssues"]}
    assert "R-4" in ids
    assert "R-5" in ids


def test_apply_refinement_audit_deletes_duplicate():
    state.SHARED_BOARD.clear()
    state.SHARED_BOARD["Refinement"] = [
        _task("R-10", "Keep me", description="Rich"),
        _task("R-11", "keep me", description=""),
    ]
    result = apply_refinement_audit_actions(delete_task_ids=["R-11"])
    assert result["deleted"] == ["R-11"]
    assert len(state.SHARED_BOARD["Refinement"]) == 1
    assert state.SHARED_BOARD["Refinement"][0]["id"] == "R-10"
