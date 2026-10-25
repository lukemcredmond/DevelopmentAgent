"""Tests for hybrid STEP_FILE_READS seeding at dev step start."""

from unittest.mock import patch

from backend import state
from backend.workspace.files import _task_declared_file_paths, seed_focus_preloaded_reads


def test_task_declared_file_paths_prioritizes_edit_target():
    task = {
        "files": ["README.md", "lib/data/sample_meals.dart", "lib/main.dart"],
        "writePaths": ["lib/features/meals/meal_list.dart"],
    }
    with patch(
        "backend.services.file_blocker.resolve_dev_edit_target_path",
        return_value="lib/main.dart",
    ):
        paths = _task_declared_file_paths(task)
    assert paths[0] == "lib/main.dart"
    assert "lib/features/meals/meal_list.dart" in paths
    assert "README.md" in paths


def test_seed_focus_preloaded_reads_records_step_reads():
    state.STEP_FILE_READS.clear()
    task = {
        "files": ["README.md", "lib/main.dart"],
    }
    with patch(
        "backend.services.file_blocker.resolve_dev_edit_target_path",
        return_value="lib/main.dart",
    ):
        with patch(
            "backend.workspace.files.resolve_workspace_path",
            side_effect=lambda p: str(p).replace("\\", "/"),
        ):
            with patch(
                "backend.workspace.files.read_workspace_file",
                side_effect=lambda p: f"content:{p}",
            ):
                seeded = seed_focus_preloaded_reads(task, max_files=4)
    assert "lib/main.dart" in seeded
    assert "README.md" in seeded
    assert state.STEP_FILE_READS.get("lib/main.dart") == "content:lib/main.dart"
