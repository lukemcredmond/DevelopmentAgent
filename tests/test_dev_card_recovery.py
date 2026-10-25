"""Dev card lint-source repair and high-churn recovery."""

from unittest.mock import patch

from backend import state
from backend.agents.task_context import init_new_task
from backend.bootstrap import initialize
from backend.services.dev_card_recovery import (
    auto_unstick_high_churn_dev_card,
    ensure_lint_source_for_feature_task,
)


def test_ensure_lint_source_replaces_main_for_shopping_title(tmp_path):
    initialize()
    state.WORKSPACE_DIR = str(tmp_path)
    lib = tmp_path / "lib" / "screens"
    lib.mkdir(parents=True)
    (lib / "shopping_list_screen.dart").write_text("// shop\n", encoding="utf-8")
    (tmp_path / "pubspec.yaml").write_text(
        "dependencies:\n  flutter:\n    sdk: flutter\n",
        encoding="utf-8",
    )
    task = {
        "title": "Shopping list — list UI and empty state",
        "description": "",
        "lintSourceFile": "lib/main.dart",
    }
    assert ensure_lint_source_for_feature_task(task) is True
    assert "shopping_list" in task["lintSourceFile"]


def test_auto_unstick_clears_defer_on_missing_target(tmp_path):
    initialize()
    state.WORKSPACE_DIR = str(tmp_path)
    lib = tmp_path / "lib" / "screens"
    lib.mkdir(parents=True)
    (lib / "shopping_list_screen.dart").write_text("// shop\n", encoding="utf-8")
    (tmp_path / "pubspec.yaml").write_text(
        "dependencies:\n  flutter:\n    sdk: flutter\n",
        encoding="utf-8",
    )
    task = init_new_task(
        {
            "id": "T-SHOP",
            "title": "Shopping list — provider",
            "description": "",
            "status": "In Progress",
            "acceptanceCriteria": ["ok"],
        }
    )
    task["lastRefusalDiagnosis"] = {"cause": "missing_edit_target"}
    task["devDeferredUntilStep"] = 99
    task["identicalPatchFailCount"] = 2
    state.SHARED_BOARD = {
        "In Progress": [task],
        "Backlog": [],
        "Needs PO": [],
        "Needs User": [],
        "Done": [],
    }
    with patch("backend.services.project_service.save_current_project_state"):
        assert auto_unstick_high_churn_dev_card("T-SHOP") is True
    live = state.SHARED_BOARD["In Progress"][0]
    assert "shopping_list" in str(live.get("lintSourceFile") or "")
    assert live.get("devDeferredUntilStep") is None
    assert int(live.get("identicalPatchFailCount") or 0) == 0
