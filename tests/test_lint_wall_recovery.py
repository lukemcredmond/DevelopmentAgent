"""Deterministic lint-wall recovery helpers."""

from backend.services.lint_wall_recovery import (
    build_lint_wall_recovery_nudge,
    detect_lint_wall_pattern,
    deterministic_lint_wall_patch,
)

_D78B576_DIAGNOSTIC = (
    "warning • The URI 'package:flutter_lints/flutter.yaml' included in "
    "'/mnt/Storage/my-agents/ai_projects/coding_output/local_meal_planner_5/analysis_options.yaml' "
    "can't be found when analyzing '/mnt/Storage/my-agents/ai_projects/coding_output/local_meal_planner_5' "
    "• analysis_options.yaml"
)


def test_detect_flutter_lints_pattern_from_diagnostic():
    task = {
        "title": "Build main app UI with tabs",
        "lastCommandDiagnostics": [{"file": _D78B576_DIAGNOSTIC}],
    }
    assert detect_lint_wall_pattern(task) == "flutter_lints_analysis_options"


def test_build_nudge_for_analysis_options():
    task = {"lastCommandDiagnostics": [{"file": _D78B576_DIAGNOSTIC}]}
    nudge = build_lint_wall_recovery_nudge(task, target_path="analysis_options.yaml")
    assert "LINT WALL RECOVERY" in nudge
    assert "analysis_options.yaml" in nudge


def test_deterministic_patch_comments_include_line():
    task = {"lastCommandDiagnostics": [{"file": _D78B576_DIAGNOSTIC}]}
    content = "include: package:flutter_lints/flutter.yaml\n"
    det = deterministic_lint_wall_patch(task, "analysis_options.yaml", content)
    assert det is not None
    path, old, new = det
    assert path == "analysis_options.yaml"
    assert old in content
    assert "disabled until flutter_lints" in new


def test_deterministic_pubspec_adds_flutter_lints():
    task = {
        "title": "Lint: pubspec.yaml",
        "lintSourceFile": "pubspec.yaml",
        "lastCommandDiagnostics": [{"message": "package:flutter_lints/flutter.yaml"}],
    }
    content = "name: app\ndev_dependencies:\n  flutter_test:\n"
    det = deterministic_lint_wall_patch(task, "pubspec.yaml", content)
    assert det is not None
    _, old, new = det
    assert "flutter_lints" in new
    assert old == "dev_dependencies:"
