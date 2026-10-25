"""Workspace repair API/service."""

from unittest.mock import MagicMock, patch

from backend import state
from backend.agents.task_context import get_task_lane, init_new_task
from backend.bootstrap import initialize
from backend.services.workspace_repair import run_workspace_repair


def test_workspace_repair_reopens_done_card_for_failing_file():
    initialize()
    task = init_new_task(
        {
            "id": "T-DONE-BAD",
            "title": "Fix lib/main.dart startup",
            "description": "d",
            "status": "Done",
            "acceptanceCriteria": ["ok"],
            "files": ["lib/main.dart"],
        }
    )
    state.SHARED_BOARD = {
        "Backlog": [],
        "In Progress": [],
        "Needs PO": [],
        "Needs User": [],
        "QA": [],
        "Done": [task],
    }
    mock_result = MagicMock()
    mock_result.outcome = "lint_findings"
    mock_result.diagnostics = [
        {"file": "lib/main.dart", "line": 10, "message": "error • undefined name"}
    ]

    with patch(
        "backend.services.workspace_repair.derive_project_lint_command",
        return_value="flutter analyze",
    ), patch(
        "backend.services.workspace_repair.run_workspace_command",
        return_value=mock_result,
    ), patch(
        "backend.services.workspace_repair.append_backlog_tasks",
        return_value="Added 1 task(s) to Backlog.",
    ):
        report = run_workspace_repair()

    assert report["ok"] is True
    assert report["findings"] == 1
    assert "T-DONE-BAD" in report["cardsReopened"]
    assert get_task_lane("T-DONE-BAD") == "In Progress"
    assert task.get("forcePatchNextDevStep") is True
