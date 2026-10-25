"""Semantic index hits filtered to workspace and Flutter scope."""

from __future__ import annotations

from pathlib import Path

from backend import state
from backend.bootstrap import initialize
from backend.storage.code_index import filter_semantic_index_hits


def test_filters_billing_py_for_flutter_workspace(tmp_path):
    initialize()
    state.WORKSPACE_DIR = str(tmp_path)
    (tmp_path / "pubspec.yaml").write_text(
        "dependencies:\n  flutter:\n    sdk: flutter\n",
        encoding="utf-8",
    )
    lib = tmp_path / "lib"
    lib.mkdir()
    (lib / "main.dart").write_text("void main() {}\n", encoding="utf-8")
    hits = [
        {"path": "app/billing.py", "content": "x", "score": 1.0},
        {"path": "lib/main.dart", "content": "y", "score": 0.9},
    ]
    filtered = filter_semantic_index_hits(hits)
    paths = [h["path"] for h in filtered]
    assert "app/billing.py" not in paths
    assert "lib/main.dart" in paths


def test_drops_hits_outside_workspace(tmp_path):
    initialize()
    state.WORKSPACE_DIR = str(tmp_path)
    (tmp_path / "lib").mkdir()
    (tmp_path / "lib" / "a.dart").write_text("//\n", encoding="utf-8")
    outside = tmp_path.parent / "other.dart"
    outside.write_text("//\n", encoding="utf-8")
    hits = [{"path": "../other.dart", "content": "z", "score": 1.0}]
    assert not filter_semantic_index_hits(hits)
