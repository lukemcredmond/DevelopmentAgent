"""Regression tests for Local AI delivery & reliability repairs."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from backend import state
from backend.agents.task_context import init_new_task, normalize_task
from backend.services import sprint_speed_gates as gates
from backend.services.diagnostics_parser import parse_dart_analyze
from backend.services.fix_verify_loop import _run_lint_once
from backend.services.focus_slice import all_focus_slices_done
from backend.services.po_clarification import should_move_off_needs_po_without_llm
from backend.services.po_task_paths import infer_write_paths_for_po_task
from backend.services.workflow_settings import DEFAULT_WORKFLOW_SETTINGS, migrate_performance_settings
from backend.workspace.files import record_step_file_read, write_workspace_file


def test_force_patch_only_after_stall_exits():
    assert not gates.should_force_patch_next_dev_step(
        {"lastStepOutcome": {"exitReason": "completed_with_writes"}}
    )
    assert not gates.should_force_patch_next_dev_step(
        {"lastStepOutcome": {"exitReason": "max_iterations_after_writes"}}
    )
    assert not gates.should_force_patch_next_dev_step(
        {"lastStepOutcome": {"exitReason": "identical_write_loop"}}
    )
    assert gates.should_force_patch_next_dev_step(
        {"lastStepOutcome": {"exitReason": "read_only_no_edits"}}
    )


def test_migrate_performance_settings_v2_verify_driven_defaults():
    old = {"performanceSettingsVersion": 1, "enableFocusMicroSteps": True}
    out = migrate_performance_settings(old)
    assert out["performanceSettingsVersion"] == 2
    assert out["enableFocusMicroSteps"] is False
    assert out["requireAcChecklistForDone"] is False
    assert out["enableFixVerifyLoop"] is True
    assert out["devEvalTimeoutSec"] == DEFAULT_WORKFLOW_SETTINGS["devEvalTimeoutSec"]


def test_infer_write_paths_skips_read_only_files():
    task = {
        "title": "Shopping list provider",
        "files": [
            {"path": "README.md", "action": "read"},
            {"path": "lib/providers/shopping_list_provider.dart", "action": "write"},
        ],
    }
    paths = infer_write_paths_for_po_task(task)
    assert "README.md" not in paths
    assert "lib/providers/shopping_list_provider.dart" in paths


def test_parse_dart_analyze_trailing_lint_code():
    out = parse_dart_analyze(
        "  error • Undefined name 'x' • lib/main.dart:10:5 • undefined_identifier\n"
    )
    assert len(out) == 1
    assert out[0]["file"] == "lib/main.dart"
    assert out[0]["line"] == 10


def test_run_lint_once_requires_exit_zero(monkeypatch):
    task = init_new_task({"id": "T-LINT", "title": "t", "description": "d"})
    normalize_task(task)
    cmd = MagicMock()
    cmd.outcome = "ok"
    cmd.exit_code = 1
    cmd.diagnostics = []
    cmd.summary = "failed"
    with patch(
        "backend.services.fix_verify_loop.run_workspace_command",
        return_value=cmd,
    ), patch(
        "backend.services.fix_verify_loop.find_task_by_id",
        return_value=task,
    ):
        _, clean = _run_lint_once("T-LINT", task, "flutter analyze")
    assert clean is False


def test_should_move_off_needs_po_respects_dev_claim_blocked():
    task = init_new_task(
        {
            "id": "T-PO",
            "title": "Big card",
            "description": "create add update verify " * 20,
            "acceptanceCriteria": ["a", "b", "c", "d", "e"],
        }
    )
    normalize_task(task)
    with patch(
        "backend.services.po_clarification.task_has_ready_spec",
        return_value=True,
    ), patch(
        "backend.services.po_clarification.po_skip_churn_should_block",
        return_value=(False, ""),
    ), patch(
        "backend.services.task_spec_validation.dev_claim_blocked",
        return_value="oversized",
    ):
        assert should_move_off_needs_po_without_llm(task) is False


def test_focus_slices_done_without_ac_checklist_when_verify_clean():
    task = init_new_task(
        {
            "id": "T-FOCUS",
            "title": "Feature",
            "description": "d",
            "acceptanceCriteria": ["ac1", "ac2"],
            "focusMode": "ac",
            "focusAcIndex": 1,
            "acChecklist": [False, False],
            "requiresDev": True,
            "workType": "implementation",
        }
    )
    normalize_task(task)
    state.FIX_VERIFY_LINT_CLEAN = True
    task["fixVerifyLintClean"] = True
    with patch(
        "backend.services.focus_slice.get_workflow_settings",
        return_value={
            **DEFAULT_WORKFLOW_SETTINGS,
            "enableFocusMicroSteps": True,
            "requireAcChecklistForDone": False,
        },
    ):
        assert all_focus_slices_done(task) is True


def test_write_workspace_file_updates_step_file_reads(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "WORKSPACE_DIR", str(tmp_path))
    state.STEP_FILE_READS.clear()
    record_step_file_read("lib/a.dart", "old")
    write_workspace_file("lib/a.dart", "new content")
    assert state.STEP_FILE_READS.get("lib/a.dart") == "new content"
