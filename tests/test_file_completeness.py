"""File completeness scanner, gates, delegation, and write warnings."""

from __future__ import annotations

import os

from backend import state
from backend.agents.task_context import init_new_task
from backend.bootstrap import initialize
from backend.services.file_completeness import (
    completeness_gate_blocks,
    find_delegated_owner,
    format_write_warning,
    scan_file_content,
    scan_task_files,
    should_skip_path,
)
from backend.services.workflow_settings import reset_workflow_settings, save_workflow_settings
from backend.workspace.files import write_workspace_file


def _setup_workspace(tmp_path):
    initialize()
    reset_workflow_settings()
    ws = str(tmp_path / "proj")
    os.makedirs(os.path.join(ws, "lib"), exist_ok=True)
    state.WORKSPACE_DIR = ws
    state.VIRTUAL_FILESYSTEM.clear()
    return ws


def test_scan_detects_todo_comment_only_file():
    content = "// TODO: Implement MealRepository with import/export functionality\n"
    findings = scan_file_content("lib/meal_repository.dart", content)
    ids = {f.pattern_id for f in findings}
    assert "todo" in ids
    assert "comment_only" in ids


def test_scan_detects_auto_scaffold_marker():
    content = "// Auto-scaffolded stub for lib/models.dart\nvoid modelsStub() {}\n"
    findings = scan_file_content("lib/models.dart", content)
    assert any(f.pattern_id == "auto_scaffold" for f in findings)


def test_scan_detects_this_file_will_handle():
    content = "// This file will handle the random meal selection logic.\n"
    findings = scan_file_content("lib/meal_selector.dart", content)
    assert any(f.pattern_id == "this_file_will" for f in findings)


def test_scan_skips_test_files():
    content = "// TODO: add tests later\n"
    assert should_skip_path("test/widget_test.dart") is True
    findings = scan_file_content("test/widget_test.dart", content)
    assert findings == []


def test_scan_allows_real_implementation():
    content = (
        "import 'dart:math';\n\n"
        "class MealSelector {\n"
        "  String suggest(List<String> meals) => meals.first;\n"
        "}\n"
    )
    findings = scan_file_content("lib/meal_selector.dart", content)
    assert findings == []


def test_dev_gate_blocks_incomplete_files(tmp_path):
    from backend.services.sprint_service import dev_gate_blocks_advance

    ws = _setup_workspace(tmp_path)
    save_workflow_settings(
        {
            "requireFileCompleteness": True,
            "requireWorkspaceStructure": False,
            "requireCleanLint": False,
        }
    )
    path = os.path.join(ws, "lib", "meal_repository.dart")
    with open(path, "w", encoding="utf-8") as f:
        f.write("// TODO: Implement MealRepository\n")

    task = init_new_task({"id": "T-INC", "title": "Repo", "description": "d"})
    task["files"] = [{"path": "lib/meal_repository.dart", "action": "written"}]

    blocked, reason = dev_gate_blocks_advance(task)
    assert blocked is True
    assert "incomplete files" in reason.lower()
    assert "meal_repository.dart" in reason


def test_dev_gate_allows_complete_files(tmp_path):
    from backend.services.sprint_service import dev_gate_blocks_advance

    ws = _setup_workspace(tmp_path)
    save_workflow_settings(
        {
            "requireFileCompleteness": True,
            "requireWorkspaceStructure": False,
            "requireCleanLint": False,
        }
    )
    path = os.path.join(ws, "lib", "meal_selector.dart")
    with open(path, "w", encoding="utf-8") as f:
        f.write(
            "import 'dart:math';\n"
            "class MealSelector {\n"
            "  String pick(List<String> meals) => meals[Random().nextInt(meals.length)];\n"
            "}\n"
        )

    task = init_new_task({"id": "T-OK", "title": "Selector", "description": "d"})
    task["files"] = [{"path": "lib/meal_selector.dart", "action": "written"}]

    blocked, reason = dev_gate_blocks_advance(task)
    assert blocked is False
    assert reason == ""


def test_stub_delegation_allows_scaffolder_when_owner_exists(tmp_path):
    ws = _setup_workspace(tmp_path)
    save_workflow_settings(
        {
            "requireFileCompleteness": True,
            "allowStubDelegation": True,
        }
    )
    stub_path = os.path.join(ws, "lib", "meal_repository.dart")
    with open(stub_path, "w", encoding="utf-8") as f:
        f.write("// Auto-scaffolded stub for lib/meal_repository.dart\nvoid meal_repositoryStub() {}\n")

    scaffolder = init_new_task({"id": "T-SCAFF", "title": "Scaffold", "description": "d"})
    scaffolder["scaffoldedFiles"] = ["lib/meal_repository.dart"]
    scaffolder["files"] = [{"path": "lib/meal_repository.dart", "action": "written"}]

    owner = init_new_task(
        {
            "id": "T-OWN",
            "title": "Implement: lib/meal_repository.dart",
            "description": "Full repository",
            "lintSourceFile": "lib/meal_repository.dart",
        }
    )
    state.SHARED_BOARD.setdefault("Backlog", [])
    state.SHARED_BOARD["Backlog"] = [owner]

    report = scan_task_files(scaffolder)
    assert report.delegated_paths == ["lib/meal_repository.dart"]
    assert report.has_blocking is False

    owner["files"] = [{"path": "lib/meal_repository.dart", "action": "written"}]
    owner_report = scan_task_files(owner)
    assert owner_report.has_blocking is True


def test_find_delegated_owner_by_lint_source():
    initialize()
    state.SHARED_BOARD.clear()
    for lane in ("Backlog", "In Progress", "Done"):
        state.SHARED_BOARD[lane] = []
    owner = init_new_task(
        {
            "id": "T-OWN2",
            "title": "Lint: lib/main.dart",
            "description": "d",
            "lintSourceFile": "lib/main.dart",
        }
    )
    state.SHARED_BOARD["Backlog"] = [owner]
    found = find_delegated_owner("lib/main.dart", "T-OTHER")
    assert found is not None
    assert found["id"] == "T-OWN2"


def test_write_workspace_file_appends_placeholder_warning(tmp_path):
    ws = _setup_workspace(tmp_path)
    state.ACTIVE_SPRINT_TASK_ID = "T-WARN"
    state.ACTIVE_SPRINT_AGENT = "Developer"

    msg = write_workspace_file(
        "lib/meal_repository.dart",
        "// TODO: Implement MealRepository with import/export functionality\n",
    )
    assert "Warning:" in msg
    assert "placeholder" in msg.lower()
    assert "meal_repository.dart" in msg


def test_format_write_warning_lists_patterns():
    findings = scan_file_content(
        "lib/x.dart",
        "// TODO: stub\n// This file will handle stuff\n",
    )
    warn = format_write_warning(findings)
    assert "Warning:" in warn
    assert "todo" in warn.lower() or "this_file_will" in warn


def test_completeness_gate_disabled():
    save_workflow_settings({"requireFileCompleteness": False})
    task = init_new_task({"id": "T-OFF", "title": "t", "description": "d"})
    task["files"] = [{"path": "lib/x.dart", "action": "written"}]
    blocked, reason = completeness_gate_blocks(task)
    assert blocked is False
    assert reason == ""


def test_done_audit_flags_incomplete_files(tmp_path, monkeypatch):
    from backend.services.done_audit import audit_single_done_task

    ws = _setup_workspace(tmp_path)
    save_workflow_settings({"requireFileCompleteness": True})
    path = os.path.join(ws, "lib", "main.dart")
    with open(path, "w", encoding="utf-8") as f:
        f.write("// TODO: Implement main\n")

    task = init_new_task({"id": "T-DONE", "title": "Main", "description": "d", "status": "Done"})
    task["files"] = [{"path": "lib/main.dart", "action": "written"}]
    row = audit_single_done_task(task)
    assert row is not None
    assert any("Incomplete files" in r for r in row.get("reasons") or [])
