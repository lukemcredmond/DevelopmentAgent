"""CommandResult.success and auto-format after edit."""

import os
from unittest.mock import patch

from backend import state
from backend.bootstrap import initialize
from backend.services.command_result import CommandResult, build_command_result
from backend.workspace.files import maybe_auto_format_after_edit, write_workspace_file


def test_command_result_success_matches_ok_outcome():
    ok = build_command_result("echo hi", exit_code=0, stdout="hi", stderr="", duration_ms=1)
    assert ok.success is True
    assert ok.outcome == "ok"

    failed = CommandResult(
        command="false",
        exit_code=1,
        stdout="",
        stderr="fail",
        duration_ms=1,
        outcome="failed",
    )
    assert failed.success is False


def test_maybe_auto_format_uses_success_without_attribute_error():
    initialize()
    ws_dir = state.WORKSPACE_DIR
    os.makedirs(ws_dir, exist_ok=True)
    with open(os.path.join(ws_dir, "pubspec.yaml"), "w", encoding="utf-8") as f:
        f.write("name: test_app\n")
    with patch("backend.workspace.files.get_workflow_settings") as mock_ws:
        mock_ws.return_value = {"autoFormatAfterEdit": True}
        with patch("backend.services.command_result.run_workspace_command") as mock_run:
            mock_run.return_value = CommandResult(
                command='dart format "lib/main.dart"',
                exit_code=0,
                stdout="Formatted 1 file",
                stderr="",
                duration_ms=10,
                outcome="ok",
            )
            note = maybe_auto_format_after_edit("lib/main.dart")
    assert note is not None
    assert "Auto-formatted" in note
    assert "AttributeError" not in note


def test_write_workspace_file_succeeds_when_auto_format_fails():
    initialize()
    ws_dir = state.WORKSPACE_DIR
    os.makedirs(ws_dir, exist_ok=True)
    with open(os.path.join(ws_dir, "pubspec.yaml"), "w", encoding="utf-8") as f:
        f.write("name: test_app\n")

    dart_path = "lib/main.dart"
    with patch("backend.workspace.files.get_workflow_settings") as mock_ws:
        mock_ws.return_value = {"autoFormatAfterEdit": True}
        with patch("backend.services.command_result.run_workspace_command") as mock_run:
            mock_run.side_effect = RuntimeError("dart not found")
            result = write_workspace_file(dart_path, "void main() {}\n")

    assert result.startswith("Successfully saved")
    assert "physical write failed" not in result
    assert "Auto-format skipped" in result
