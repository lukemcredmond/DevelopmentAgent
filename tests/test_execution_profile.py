"""Dev-first over skippable PO, junk lint fanout, implementer profile."""

from unittest.mock import patch

from backend import state
from backend.agents.task_context import get_task_lane, init_new_task
from backend.bootstrap import initialize
from backend.services.lint_fanout import (
    is_junk_lint_path,
    maybe_fanout_lint_diagnostics,
    retire_junk_lint_cards,
)
from backend.services.sprint_service import (
    _check_stuck_and_escalate,
    _dev_needs_po,
    _select_sprint_step_handler,
    _run_po_clarification,
)
from backend.services.workflow_settings import (
    DEFAULT_WORKFLOW_SETTINGS,
    get_execution_profile,
    reset_workflow_settings,
    save_workflow_settings,
)


def teardown_function():
    state.PENDING_SIMULATION = None
    state.DEV_STEP_INTERRUPTED = False
    state.SPRINT_CANCEL = False
    state.SPRINT_CANCEL_INTENT = None
    reset_workflow_settings()


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
        "Blocked": [],
    }
    base.update(lanes)
    return base


def _spec_task(task_id: str, title: str, status: str) -> dict:
    task = init_new_task(
        {
            "id": task_id,
            "title": title,
            "description": "Fix the diagnostic in the named file.",
            "status": status,
            "acceptanceCriteria": ["File compiles", "Lint clean for this file"],
        }
    )
    task["lastStepOutcome"] = {"exitReason": "llm_call_failed"}
    return task


def test_default_execution_profile_is_scrum():
    from pathlib import Path

    assert DEFAULT_WORKFLOW_SETTINGS.get("executionProfile") == "scrum"
    initialize()
    reset_workflow_settings()
    assert get_execution_profile() == "scrum"
    wf = (Path(__file__).resolve().parents[1] / "frontend" / "src" / "components" / "WorkflowPanel.tsx").read_text(
        encoding="utf-8"
    )
    assert "executionProfile" in wf
    assert "Implementer" in wf


def test_handler_prefers_dev_when_needs_po_would_skip():
    initialize()
    reset_workflow_settings()
    npo = _spec_task("T-NPO", "Lint: lib/a.dart", "Needs PO")
    ip = _spec_task("T-DEV", "Lint: lib/b.dart", "In Progress")
    state.SHARED_BOARD = _empty_board(**{"Needs PO": [npo], "In Progress": [ip]})
    handler, active = _select_sprint_step_handler()
    assert handler == "dev"
    assert active and active["id"] == "T-DEV"


def test_handler_runs_po_when_spec_missing():
    initialize()
    reset_workflow_settings()
    npo = init_new_task(
        {"id": "T-NOSPEC", "title": "Vague", "description": "", "status": "Needs PO"}
    )
    npo["acceptanceCriteria"] = []
    ip = _spec_task("T-DEV2", "Work", "In Progress")
    state.SHARED_BOARD = _empty_board(**{"Needs PO": [npo], "In Progress": [ip]})
    handler, active = _select_sprint_step_handler()
    assert handler == "po"
    assert active and active["id"] == "T-NOSPEC"


def test_implementer_skips_po_when_spec_present_and_dev_runnable():
    initialize()
    reset_workflow_settings()
    save_workflow_settings({"executionProfile": "implementer"})
    npo = _spec_task("T-NPO-I", "Lint: lib/a.dart", "Needs PO")
    ip = _spec_task("T-DEV-I", "Lint: lib/b.dart", "In Progress")
    state.SHARED_BOARD = _empty_board(**{"Needs PO": [npo], "In Progress": [ip]})
    handler, active = _select_sprint_step_handler()
    assert handler == "dev"
    assert active and active["id"] == "T-DEV-I"


def test_implementer_prefers_dev_over_incomplete_needs_po():
    initialize()
    reset_workflow_settings()
    save_workflow_settings({"executionProfile": "implementer"})
    npo = init_new_task(
        {"id": "T-VAGUE-I", "title": "Vague", "description": "", "status": "Needs PO"}
    )
    npo["acceptanceCriteria"] = []
    ip = _spec_task("T-DEV-I2", "Work", "In Progress")
    state.SHARED_BOARD = _empty_board(**{"Needs PO": [npo], "In Progress": [ip]})
    handler, active = _select_sprint_step_handler()
    assert handler == "dev"
    assert active and active["id"] == "T-DEV-I2"


def test_po_skip_chains_developer():
    initialize()
    reset_workflow_settings()
    task = _spec_task("T-CHAIN", "Lint: lib/c.dart", "Needs PO")
    state.SHARED_BOARD = _empty_board(**{"Needs PO": [task]})
    with patch("backend.services.sprint_service._run_developer_step") as dev:
        _run_po_clarification(task, "brief")
    dev.assert_called_once()
    assert get_task_lane("T-CHAIN") == "In Progress"


def test_llm_fail_with_spec_stays_in_progress():
    initialize()
    reset_workflow_settings()
    save_workflow_settings({"maxStuckSteps": 2, "maxPoRoundTrips": 3})
    task = _spec_task("T-LLM", "Lint: lib/d.dart", "In Progress")
    task["stuckLoops"] = 1
    state.SHARED_BOARD = _empty_board(**{"In Progress": [task]})
    _check_stuck_and_escalate("T-LLM", "In Progress", agent_key="dev")
    assert get_task_lane("T-LLM") == "In Progress"
    assert task.get("forcePatchNextDevStep") is True


def test_junk_lint_path_and_fanout_skips_pub_cache():
    initialize()
    reset_workflow_settings()
    save_workflow_settings(
        {"maxInCardLintFixes": 1, "lintFanoutThreshold": 2, "maxLintFanoutCards": 8}
    )
    assert is_junk_lint_path(
        "/home/x/.pub-cache/hosted/pub.dev/sqflite_common-2.5.11/lib/sqlite_api.dart"
    )
    assert not is_junk_lint_path("lib/presentation/store.dart")
    task = init_new_task({"id": "T-FAN", "title": "Feature", "description": "d"})
    state.SHARED_BOARD = _empty_board(**{"In Progress": [task]})
    diags = [
        {
            "file": "/home/x/.pub-cache/hosted/pub.dev/sqflite_common-2.5.11/lib/sqlite_api.dart",
            "line": 1,
            "severity": "error",
            "message": "x",
        },
        {"file": "lib/a.dart", "line": 1, "severity": "error", "message": "a"},
        {"file": "lib/b.dart", "line": 1, "severity": "error", "message": "b"},
        {"file": "lib/c.dart", "line": 1, "severity": "error", "message": "c"},
    ]
    result = maybe_fanout_lint_diagnostics(task, diags, step_marker="step-junk")
    spawned_files = [
        t.get("lintSourceFile")
        for t in state.SHARED_BOARD.get("Backlog", [])
        if str(t.get("title") or "").startswith("Lint:")
    ]
    assert all(".pub-cache" not in str(p) for p in spawned_files)
    assert result["skipped"] != "below_threshold" or not spawned_files


def test_retire_junk_lint_cards():
    initialize()
    reset_workflow_settings()
    junk = init_new_task(
        {
            "id": "T-JUNK",
            "title": "Lint: /home/x/.pub-cache/hosted/pub.dev/sqflite_common-2.5.11/lib/sqlite_api.dart",
            "description": "d",
            "status": "Needs PO",
        }
    )
    junk["lintSourceFile"] = (
        "/home/x/.pub-cache/hosted/pub.dev/sqflite_common-2.5.11/lib/sqlite_api.dart"
    )
    keep = init_new_task(
        {
            "id": "T-KEEP",
            "title": "Lint: lib/app.dart",
            "description": "d",
            "status": "Needs PO",
        }
    )
    keep["lintSourceFile"] = "lib/app.dart"
    state.SHARED_BOARD = _empty_board(**{"Needs PO": [junk, keep]})
    n = retire_junk_lint_cards()
    assert n == 1
    assert get_task_lane("T-JUNK") == "Done"
    assert get_task_lane("T-KEEP") == "Needs PO"


def test_implementer_disables_fanout():
    initialize()
    reset_workflow_settings()
    save_workflow_settings(
        {
            "executionProfile": "implementer",
            "maxInCardLintFixes": 1,
            "lintFanoutThreshold": 2,
            "maxLintFanoutCards": 8,
        }
    )
    task = init_new_task({"id": "T-IMP", "title": "Feature", "description": "d"})
    state.SHARED_BOARD = _empty_board(**{"In Progress": [task]})
    diags = [
        {"file": f"lib/f{i}.dart", "line": i, "severity": "error", "message": "x"}
        for i in range(8)
    ]
    result = maybe_fanout_lint_diagnostics(task, diags, step_marker="step-imp")
    assert result["skipped"] == "fanout_disabled"
    assert state.SHARED_BOARD.get("Backlog") == []


def test_implementer_dev_does_not_escalate_to_po():
    initialize()
    reset_workflow_settings()
    save_workflow_settings({"executionProfile": "implementer"})
    task = _spec_task("T-NOESC", "Work", "In Progress")
    assert (
        _dev_needs_po("I will move the task to 'Needs PO' for more detail.", task) is False
    )
