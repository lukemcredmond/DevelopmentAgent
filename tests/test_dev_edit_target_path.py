"""Safe dev edit target path resolution (synthetic read fallback)."""

from backend.services.file_blocker import (
    infer_lint_source_file,
    resolve_dev_edit_target_path,
    validate_dev_edit_target_path,
)

_D78B576_DIAGNOSTIC = (
    "warning • The URI 'package:flutter_lints/flutter.yaml' included in "
    "'/mnt/Storage/my-agents/ai_projects/coding_output/local_meal_planner_5/analysis_options.yaml' "
    "can't be found when analyzing '/mnt/Storage/my-agents/ai_projects/coding_output/local_meal_planner_5' "
    "• analysis_options.yaml"
)


def test_validate_rejects_formatted_lint_diagnostic_line():
    assert validate_dev_edit_target_path(_D78B576_DIAGNOSTIC) is False


def test_resolve_extracts_trailing_filename_from_diagnostic():
    task = {
        "title": "Build main app UI with tabs",
        "lastCommandDiagnostics": [{"file": _D78B576_DIAGNOSTIC}],
    }
    assert resolve_dev_edit_target_path(task) == "analysis_options.yaml"


def test_resolve_prefers_write_paths_over_diagnostics():
    task = {
        "title": "Build main app UI with tabs",
        "writePaths": ["lib/main.dart"],
        "lastCommandDiagnostics": [{"file": _D78B576_DIAGNOSTIC}],
    }
    assert resolve_dev_edit_target_path(task) == "lib/main.dart"


def test_infer_lint_source_from_flutter_lints_diagnostic():
    task = {
        "title": "Build main app UI with tabs",
        "lastCommandDiagnostics": [{"file": _D78B576_DIAGNOSTIC}],
    }
    assert infer_lint_source_file(task) == "analysis_options.yaml"


def test_resolve_lint_wall_uses_lint_source_file():
    task = {
        "title": "Lint: pubspec.yaml",
        "lintSourceFile": "pubspec.yaml",
        "lastCommandDiagnostics": [{"file": _D78B576_DIAGNOSTIC}],
    }
    assert resolve_dev_edit_target_path(task) == "pubspec.yaml"
