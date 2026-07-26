"""Hybrid lint fan-out: budget on card, leftovers → related Backlog by file."""

from __future__ import annotations

from pathlib import Path

from backend.bootstrap import initialize
from backend.agents.task_context import find_task_by_id, get_task_lane, init_new_task
from backend.services.lint_fanout import (
    budget_diagnostics,
    maybe_fanout_lint_diagnostics,
    sort_diagnostics,
)
from backend.services.workflow_settings import reset_workflow_settings, save_workflow_settings


def _diag(file: str, line: int, severity: str = "error", message: str = "x") -> dict:
    return {
        "file": file,
        "line": line,
        "column": 1,
        "severity": severity,
        "message": message,
    }


def _empty_board(**lanes):
    base = {
        "Features": [],
        "Backlog": [],
        "Refinement": [],
        "In Progress": [],
        "Needs PO": [],
        "Needs User": [],
        "Code Review": [],
        "QA": [],
        "Done": [],
        "Pending Approval": [],
    }
    base.update(lanes)
    return base


def test_budget_keeps_highest_severity_first():
    diags = [
        _diag("a.dart", 1, "info", "i"),
        _diag("b.dart", 2, "error", "e"),
        _diag("c.dart", 3, "warning", "w"),
    ]
    ordered = sort_diagnostics(diags)
    assert ordered[0]["severity"] == "error"
    kept, rest = budget_diagnostics(diags, max_keep=2)
    assert len(kept) == 2
    assert kept[0]["severity"] == "error"
    assert len(rest) == 1


def test_below_threshold_no_spawn():
    from backend import state

    initialize()
    reset_workflow_settings()
    save_workflow_settings(
        {"maxInCardLintFixes": 5, "lintFanoutThreshold": 6, "maxLintFanoutCards": 8}
    )
    task = init_new_task({"id": "T-LINT1", "title": "Feature", "description": "d"})
    state.SHARED_BOARD = _empty_board(**{"In Progress": [task]})
    diags = [_diag(f"lib/f{i}.dart", i) for i in range(5)]
    result = maybe_fanout_lint_diagnostics(task, diags, step_marker="step-a")
    assert result["skipped"] == "below_threshold"
    assert result["spawned"] == []
    assert len(state.SHARED_BOARD["Backlog"]) == 0


def test_fanout_groups_by_file_and_links_related():
    from backend import state

    initialize()
    reset_workflow_settings()
    save_workflow_settings(
        {
            "maxInCardLintFixes": 2,
            "lintFanoutThreshold": 3,
            "maxLintFanoutCards": 8,
            "requireBacklogRefinement": False,
            "requireBacklogApproval": False,
        }
    )
    task = init_new_task({"id": "T-LINT2", "title": "Shopping", "description": "feature work"})
    state.SHARED_BOARD = _empty_board(**{"In Progress": [task]})
    diags = [
        _diag("lib/a.dart", 1, "error", "e1"),
        _diag("lib/a.dart", 2, "error", "e2"),
        _diag("lib/b.dart", 3, "warning", "w1"),
        _diag("lib/c.dart", 4, "info", "i1"),
        _diag("lib/c.dart", 5, "error", "e3"),
    ]
    result = maybe_fanout_lint_diagnostics(task, diags, step_marker="step-b")
    assert len(result["kept"]) == 2
    assert len(result["spawned"]) >= 1
    parent = find_task_by_id("T-LINT2")
    assert parent is not None
    assert get_task_lane("T-LINT2") == "In Progress"
    assert len(parent.get("lastCommandDiagnostics") or []) == 2
    related = set(parent.get("relatedTaskIds") or [])
    for sid in result["spawned"]:
        assert sid in related
        child = find_task_by_id(sid)
        assert child is not None
        assert "T-LINT2" in (child.get("relatedTaskIds") or [])
        assert child.get("lintSourceFile")
        assert get_task_lane(sid) in ("Backlog", "Refinement", "Pending Approval")


def test_fanout_dedupes_same_file():
    from backend import state

    initialize()
    reset_workflow_settings()
    save_workflow_settings(
        {
            "maxInCardLintFixes": 1,
            "lintFanoutThreshold": 2,
            "maxLintFanoutCards": 8,
            "requireBacklogRefinement": False,
            "requireBacklogApproval": False,
        }
    )
    task = init_new_task({"id": "T-LINT3", "title": "Feat", "description": "d"})
    state.SHARED_BOARD = _empty_board(**{"In Progress": [task]})
    diags = [
        _diag("lib/only.dart", 1, "error", "e1"),
        _diag("lib/only.dart", 2, "error", "e2"),
        _diag("lib/only.dart", 3, "warning", "w1"),
    ]
    first = maybe_fanout_lint_diagnostics(task, diags, step_marker="step-1")
    assert len(first["spawned"]) == 1
    # Reset marker to allow second call; should dedupe by lintSourceFile
    task["lintFanoutStepMarker"] = None
    parent = find_task_by_id("T-LINT3")
    parent["lintFanoutStepMarker"] = None
    # Restore full diagnostics as if analyze ran again
    second = maybe_fanout_lint_diagnostics(parent, diags, step_marker="step-2")
    lint_cards = [
        t
        for lane in state.SHARED_BOARD.values()
        for t in lane
        if isinstance(t, dict) and t.get("lintSourceFile") == "lib/only.dart"
    ]
    assert len(lint_cards) == 1
    assert second["spawned"] == [] or second.get("skippedDuplicates", 0) >= 1


def test_prompt_markers_budget_language():
    root = Path(__file__).resolve().parents[1]
    sprint = (root / "backend" / "services" / "sprint_service.py").read_text(encoding="utf-8")
    assert "in-card lint budget" in sprint
    assert "fix each file:line listed in the Problems section" not in sprint
    fv = (root / "backend" / "services" / "fix_verify_loop.py").read_text(encoding="utf-8")
    assert "in-card budget" in fv
    assert "Fix every file:line" not in fv
    agent = (root / "backend" / "agents" / "scrum_agent.py").read_text(encoding="utf-8")
    assert "in-card lint budget" in agent
    assert "fix each file:line listed above before re-running" not in agent
    readme = (root / "README.md").read_text(encoding="utf-8")
    assert "lintFanoutThreshold" in readme
    assert "maxInCardLintFixes" in readme


def test_default_settings():
    from backend.services.workflow_settings import DEFAULT_WORKFLOW_SETTINGS

    assert DEFAULT_WORKFLOW_SETTINGS.get("maxInCardLintFixes") == 5
    assert DEFAULT_WORKFLOW_SETTINGS.get("maxLintFanoutCards") == 8
    assert DEFAULT_WORKFLOW_SETTINGS.get("lintFanoutThreshold") == 6
