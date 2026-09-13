"""Workspace path plausibility and path-tool retry limits."""

import pytest

from backend import state
from backend.workspace.files import read_workspace_file, resolve_workspace_path


@pytest.fixture(autouse=True)
def _workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "WORKSPACE_DIR", str(tmp_path))
    state.STEP_PATH_TOOL_FAILURES.clear()
    yield


def test_resolve_rejects_garbled_trailing_dot_path():
    with pytest.raises(ValueError, match="corrupted text"):
        resolve_workspace_path("망.")


def test_read_file_garbled_path_fails_and_escalates_on_repeat():
    out1 = read_workspace_file("망.")
    assert "Invalid path" in out1 or "corrupted" in out1
    assert "do not retry" in out1.lower()
    out2 = read_workspace_file("망.")
    assert "STOP calling read_file" in out2


def test_resolve_allows_normal_paths(tmp_path):
    (tmp_path / "lib").mkdir()
    (tmp_path / "lib" / "main.dart").write_text("// ok", encoding="utf-8")
    assert resolve_workspace_path("lib/main.dart") == "lib/main.dart"
    content = read_workspace_file("lib/main.dart")
    assert "// ok" in content


def test_resolve_strips_project_slug_and_nested_duplicate(tmp_path, monkeypatch):
    from backend import state

    monkeypatch.setattr(state, "PROJECT_NAME", "mealplanner")
    (tmp_path / "lib").mkdir()
    (tmp_path / "lib" / "main.dart").write_text("// root", encoding="utf-8")
    nested = tmp_path / "mealplanner"
    nested.mkdir()
    (nested / "pubspec.yaml").write_text("name: mealplanner\n", encoding="utf-8")
    (nested / "lib").mkdir()
    (nested / "lib" / "dup.dart").write_text("// nested", encoding="utf-8")

    assert resolve_workspace_path("mealplanner/lib/dup.dart") == "lib/dup.dart"
    abs_nested = str(nested / "lib" / "dup.dart")
    assert resolve_workspace_path(abs_nested) == "lib/dup.dart"


def test_resolve_strips_nested_duplicate_even_without_slug(tmp_path, monkeypatch):
    from backend import state

    monkeypatch.setattr(state, "PROJECT_NAME", "Other App")
    (tmp_path / "lib").mkdir()
    (tmp_path / "lib" / "main.dart").write_text("// root", encoding="utf-8")
    nested = tmp_path / "app_copy"
    nested.mkdir()
    (nested / "pubspec.yaml").write_text("name: app_copy\n", encoding="utf-8")
    (nested / "lib").mkdir()
    (nested / "lib" / "dup.dart").write_text("// nested", encoding="utf-8")
    assert resolve_workspace_path("app_copy/lib/dup.dart") == "lib/dup.dart"
    pkg = tmp_path / "packages" / "foo" / "lib"
    pkg.mkdir(parents=True)
    (pkg / "bar.dart").write_text("// pkg", encoding="utf-8")
    assert resolve_workspace_path("packages/foo/lib/bar.dart") == "packages/foo/lib/bar.dart"
