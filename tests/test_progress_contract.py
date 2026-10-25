"""Progress contract: writePaths, edit target prompt, implementer wall defaults."""

from backend.bootstrap import initialize
from backend.services.composer_dev_step import run_composer_dev_step
from backend.services.po_task_paths import (
    build_edit_target_system_block,
    ensure_dev_write_paths_before_step,
    maybe_queue_oversize_split,
    stamp_write_paths_on_po_task,
)
from backend.services.workflow_settings import DEFAULT_WORKFLOW_SETTINGS, get_workflow_settings
from unittest.mock import MagicMock, patch


def test_implementer_wall_default_600():
    initialize()
    assert int(DEFAULT_WORKFLOW_SETTINGS.get("implementerMaxDevStepWallSec") or 0) == 600
    ws = get_workflow_settings()
    assert int(ws.get("implementerMaxDevStepWallSec") or 0) == 600


def test_composer_dev_step_wall_fallback_600():
    initialize()
    agent = MagicMock()
    agent.execute_step.return_value = "done"
    task = {"id": "T-WALL-DEF", "title": "t"}

    with patch("backend.services.composer_dev_step.get_workflow_settings") as mock_ws:
        mock_ws.return_value = {}
        run_composer_dev_step(agent, task, "prompt", max_iterations=3, task_id="T-WALL-DEF")

    _args, kwargs = agent.execute_step.call_args
    assert kwargs.get("max_step_duration_sec") == 600


def test_build_edit_target_system_block():
    task = {"writePaths": ["lib/features/meals/meal_list.dart"]}
    block = build_edit_target_system_block(task)
    assert "EDIT TARGET" in block
    assert "lib/features/meals/meal_list.dart" in block


def test_maybe_queue_oversize_split_flags_many_ac():
    task = {
        "acceptanceCriteria": ["a", "b", "c"],
        "writePaths": ["lib/a.dart"],
    }
    assert maybe_queue_oversize_split(task) is True
    assert task.get("pendingSplit") is True


def test_stamp_write_paths_from_title_lib_path():
    task = {
        "title": "Wire lib/features/foo/bar.dart for navigation",
        "description": "",
    }
    added = stamp_write_paths_on_po_task(task)
    assert "lib/features/foo/bar.dart" in (task.get("writePaths") or [])
    assert added or task.get("writePaths")


def test_ensure_dev_write_paths_before_step_on_board():
    initialize()
    from backend import state
    from backend.agents.task_context import init_new_task

    task = init_new_task(
        {
            "id": "T-PATHS",
            "title": "Meal library — random meal selection action",
            "description": "Add button in lib/features/meals/meal_library_screen.dart",
            "acceptanceCriteria": ["Random pick works"],
        }
    )
    state.SHARED_BOARD["In Progress"] = [task]
    with patch(
        "backend.services.file_blocker.infer_feature_edit_path_from_title",
        return_value="lib/features/meals/meal_library_screen.dart",
    ):
        added = ensure_dev_write_paths_before_step("T-PATHS")
    assert added or task.get("writePaths")
