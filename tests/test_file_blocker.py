"""Shared-file blocker orchestration."""

from unittest.mock import patch

from backend import state
from backend.agents.task_context import get_task_lane, init_new_task
from backend.bootstrap import initialize
from backend.services.board_service import move_board_stage
from backend.services.file_blocker import (
    BLOCKED_BY_KIND_FILE_FIX,
    dev_edit_path_allowed_for_tool,
    dev_read_path_allowed_for_tool,
    orchestrate_file_blocker,
    reconcile_file_blockers_from_board,
    resolve_dev_edit_target_path,
    validate_dev_edit_target_path,
)
from backend.services.workflow_settings import save_workflow_settings


def _empty_board():
    initialize()
    state.SHARED_BOARD.clear()
    for lane in (
        "Features",
        "Backlog",
        "Pending Approval",
        "Refinement",
        "Blocked",
        "In Progress",
        "Needs PO",
        "Needs User",
        "Code Review",
        "QA",
        "Done",
    ):
        state.SHARED_BOARD[lane] = []


def _settings():
    return {
        "enableBlockedLane": True,
        "enableFileBlockerOrchestration": True,
        "fileBlockerMinDependents": 2,
        "fileBlockerAutoCreateFixCard": True,
        "requireBacklogRefinement": False,
    }


def test_orchestrate_creates_fix_card_and_blocks_dependents():
    _empty_board()
    save_workflow_settings(_settings())
    dep1 = init_new_task(
        {
            "id": "LINT-A",
            "title": "Lint: lib/main.dart",
            "description": "d",
            "status": "In Progress",
            "lintSourceFile": "lib/main.dart",
            "lastCommandDiagnostics": [
                {"file": "lib/main.dart", "line": 1, "message": "error", "severity": "error"}
            ],
        }
    )
    dep2 = init_new_task(
        {
            "id": "LINT-B",
            "title": "Lint: lib/main.dart",
            "description": "d",
            "status": "Needs User",
            "lintSourceFile": "lib/main.dart",
            "userQuestion": "Which lint error?",
            "needsUserKind": "lint",
        }
    )
    state.SHARED_BOARD["In Progress"] = [dep1]
    state.SHARED_BOARD["Needs User"] = [dep2]

    with patch("backend.services.file_blocker.get_workflow_settings", return_value=_settings()):
        result = orchestrate_file_blocker(
            file_path="lib/main.dart",
            source_task_id="LINT-A",
            reason="test",
        )

    assert result["fixTaskId"]
    assert len(result["blocked"]) == 1
    assert get_task_lane("LINT-B") == "Blocked"
    assert dep2.get("blockedByKind") == BLOCKED_BY_KIND_FILE_FIX
    assert result["fixTaskId"] in (dep2.get("blockedBy") or [])
    assert dep2.get("userQuestion") is None


def test_blocked_lane_releases_when_fix_card_done():
    _empty_board()
    save_workflow_settings(_settings())
    fix = init_new_task(
        {
            "id": "FIX-1",
            "title": "Lint: lib/widget.dart",
            "description": "d",
            "status": "In Progress",
            "lintSourceFile": "lib/widget.dart",
            "fileFixFor": "lib/widget.dart",
        }
    )
    dep = init_new_task(
        {
            "id": "DEP-1",
            "title": "Lint: lib/widget.dart",
            "description": "d",
            "status": "Blocked",
            "lintSourceFile": "lib/widget.dart",
            "blockedBy": ["FIX-1"],
            "blockedByKind": BLOCKED_BY_KIND_FILE_FIX,
            "blockedReturnLane": "In Progress",
        }
    )
    state.SHARED_BOARD["In Progress"] = [fix]
    state.SHARED_BOARD["Blocked"] = [dep]

    move_board_stage("FIX-1", "Done")
    assert get_task_lane("DEP-1") == "In Progress"


def test_reconcile_groups_needs_user_lint_pile():
    _empty_board()
    save_workflow_settings(_settings())
    cards = []
    for i in range(3):
        t = init_new_task(
            {
                "id": f"LINT-{i}",
                "title": "Lint: lib/main.dart",
                "description": "d",
                "status": "Needs User",
                "lintSourceFile": "lib/main.dart",
                "userQuestion": "fix lint?",
            }
        )
        cards.append(t)
    state.SHARED_BOARD["Needs User"] = cards

    with patch("backend.services.file_blocker.get_workflow_settings", return_value=_settings()):
        result = reconcile_file_blockers_from_board()

    assert result["blocked"] >= 2
    blocked_count = len(state.SHARED_BOARD.get("Blocked") or [])
    assert blocked_count >= 2


def test_resolve_dev_edit_target_lib_main_without_extension():
    task = {
        "title": "Scaffold polish — analyzer clean on lib/main and app entry",
        "description": "",
    }
    path = resolve_dev_edit_target_path(task)
    assert path == "lib/main.dart"
    assert task.get("lintSourceFile") == "lib/main.dart"


def test_resolve_dev_edit_target_prefers_code_over_readme(tmp_path):
    initialize()
    state.WORKSPACE_DIR = str(tmp_path)
    lib = tmp_path / "lib"
    lib.mkdir(parents=True)
    (lib / "app.dart").write_text("// app\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("# app\n", encoding="utf-8")
    task = {
        "title": "Polish feature",
        "description": "",
        "scaffoldedFiles": ["README.md", "lib/app.dart"],
    }
    path = resolve_dev_edit_target_path(task)
    assert path == "lib/app.dart"
    assert task.get("lintSourceFile") == "lib/app.dart"


def test_resolve_dev_edit_target_scaffold_offline_uses_main_dart(tmp_path):
    initialize()
    state.WORKSPACE_DIR = str(tmp_path)
    lib = tmp_path / "lib"
    lib.mkdir(parents=True)
    (lib / "main.dart").write_text("void main() {}\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("# app\n", encoding="utf-8")
    task = {
        "title": "Scaffold polish — offline persistence edge cases",
        "description": "",
        "scaffoldedFiles": ["README.md"],
    }
    path = resolve_dev_edit_target_path(task)
    assert path == "lib/main.dart"
    assert task.get("lintSourceFile") == "lib/main.dart"


def test_flutter_workspace_rejects_billing_py_read(tmp_path):
    initialize()
    state.WORKSPACE_DIR = str(tmp_path)
    (tmp_path / "pubspec.yaml").write_text(
        "name: meal\nenvironment:\n  sdk: '>=3.0.0 <4.0.0'\ndependencies:\n  flutter:\n    sdk: flutter\n",
        encoding="utf-8",
    )
    task = {
        "title": "Navigation — MealLibraryScreen route",
        "description": "Wire MealLibraryScreen in router",
        "lintSourceFile": "lib/main.dart",
    }
    assert not validate_dev_edit_target_path("app/billing.py")
    ok, err = dev_read_path_allowed_for_tool(task, "app/billing.py")
    assert not ok
    assert "Dart scope" in err
    ok_main, _ = dev_read_path_allowed_for_tool(task, "lib/main.dart")
    assert ok_main


def test_integration_card_allows_multi_file_edits(tmp_path):
    initialize()
    state.WORKSPACE_DIR = str(tmp_path)
    (tmp_path / "pubspec.yaml").write_text("name: x\n", encoding="utf-8")
    lib = tmp_path / "lib"
    lib.mkdir(parents=True)
    (lib / "main.dart").write_text("void main() {}\n", encoding="utf-8")
    task = {
        "title": "Repo integration",
        "lintSourceFile": "lib/main.dart",
        "integrationEditPaths": ["lib/main.dart", "pubspec.yaml"],
    }
    ok_main, _ = dev_edit_path_allowed_for_tool(task, "lib/main.dart")
    ok_pub, _ = dev_edit_path_allowed_for_tool(task, "pubspec.yaml")
    assert ok_main and ok_pub


def test_fix_card_gets_multi_path_write_paths():
    from backend.services.file_blocker import _create_fix_card_payload

    touching = [
        (
            "In Progress",
            {
                "lintSourceFile": "lib/main.dart",
                "writePaths": ["lib/main.dart"],
            },
        ),
        (
            "In Progress",
            {
                "lintSourceFile": "lib/screens/a.dart",
                "writePaths": ["lib/screens/a.dart"],
            },
        ),
    ]
    card = _create_fix_card_payload(
        "lib/main.dart",
        [{"file": "lib/main.dart", "line": 1, "message": "e", "severity": "error"}],
        "SRC-1",
        touching=touching,
    )
    paths = card.get("writePaths") or []
    assert "lib/main.dart" in paths
    assert "lib/screens/a.dart" in paths
    assert card.get("integrationEditPaths")


def test_resolve_prefers_shopping_screen_over_main_scaffold(tmp_path):
    initialize()
    state.WORKSPACE_DIR = str(tmp_path)
    (tmp_path / "pubspec.yaml").write_text(
        "dependencies:\n  flutter:\n    sdk: flutter\n",
        encoding="utf-8",
    )
    lib = tmp_path / "lib"
    screens = lib / "screens"
    screens.mkdir(parents=True)
    (lib / "main.dart").write_text("void main() {}\n", encoding="utf-8")
    (screens / "shopping_list_screen.dart").write_text("// s\n", encoding="utf-8")
    task = {
        "title": "Shopping list — empty state polish",
        "description": "scaffold offline edge",
        "scaffoldedFiles": ["README.md"],
    }
    path = resolve_dev_edit_target_path(task)
    assert path == "lib/screens/shopping_list_screen.dart"
    assert task.get("lintSourceFile") == "lib/screens/shopping_list_screen.dart"
