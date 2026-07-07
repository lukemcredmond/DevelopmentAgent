"""Code index preflight and scan tests."""

from unittest.mock import MagicMock, patch

from backend.bootstrap import initialize
from backend.storage.code_index import CodeIndexEngine
from backend.workspace.files import scan_indexable_workspace_files


def test_scan_indexable_skips_node_modules(tmp_path, monkeypatch):
    initialize()
    from backend import state

    ws = tmp_path / "proj"
    (ws / "lib").mkdir(parents=True)
    (ws / "node_modules" / "pkg").mkdir(parents=True)
    (ws / "lib" / "main.dart").write_text("void main() {}", encoding="utf-8")
    (ws / "node_modules" / "pkg" / "index.js").write_text("x", encoding="utf-8")
    monkeypatch.setattr(state, "WORKSPACE_DIR", str(ws))

    files, skipped = scan_indexable_workspace_files()
    assert "lib/main.dart" in files
    assert skipped >= 1
    assert not any("node_modules" in p for p in files)


@patch.object(CodeIndexEngine, "_verify_embed_model", return_value="Embed model missing")
def test_index_workspace_fails_preflight(mock_verify):
    initialize()
    engine = CodeIndexEngine(ollama_url="http://localhost:11434")
    result = engine.index_workspace()
    assert result["ok"] is False
    assert "Embed model" in result["error"]


@patch.object(CodeIndexEngine, "_embed", return_value=None)
@patch.object(CodeIndexEngine, "_verify_embed_model", return_value=None)
@patch.object(CodeIndexEngine, "_get_client")
def test_index_workspace_zero_chunks_is_error(mock_client, mock_preflight, mock_embed, tmp_path, monkeypatch):
    initialize()
    from backend import state

    ws = tmp_path / "proj"
    (ws / "lib").mkdir(parents=True)
    (ws / "lib" / "main.dart").write_text("void main() {}", encoding="utf-8")
    monkeypatch.setattr(state, "WORKSPACE_DIR", str(ws))

    mock_client.return_value = MagicMock()
    engine = CodeIndexEngine(ollama_url="http://localhost:11434")
    result = engine.index_workspace()
    assert result["ok"] is False
    assert result["chunks"] == 0
