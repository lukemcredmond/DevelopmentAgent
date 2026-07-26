"""Tests for RAG hybrid search, memory lessons, loop observe/episode, training export."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from backend.bootstrap import initialize
from backend.services.llm_context import prune_messages_if_needed
from backend.services.training_export import export_training_jsonl, step_trace_to_export_row
from backend.services.workflow_settings import DEFAULT_WORKFLOW_SETTINGS, reset_workflow_settings
from backend.storage.code_index import (
    CodeIndexEngine,
    _mmr_path_diverse,
    _rrf_fuse,
    build_semantic_sprint_context,
)


def test_rrf_fuse_merges_dense_and_lexical():
    dense = [
        {"path": "a.py", "startLine": 1, "endLine": 2, "content": "foo", "score": 0.9, "source": "dense"},
    ]
    lexical = [
        {"path": "b.py", "startLine": 1, "endLine": 2, "content": "bar", "score": 5, "source": "lexical"},
        {"path": "a.py", "startLine": 1, "endLine": 2, "content": "foo", "score": 3, "source": "lexical"},
    ]
    fused = _rrf_fuse(dense, lexical, limit=5)
    assert len(fused) >= 2
    paths = {h["path"] for h in fused}
    assert "a.py" in paths and "b.py" in paths
    a_hit = next(h for h in fused if h["path"] == "a.py")
    assert a_hit.get("source") == "hybrid"


def test_mmr_path_diverse_prefers_unique_paths():
    hits = [
        {"path": "a.py", "score": 1.0},
        {"path": "a.py", "score": 0.9},
        {"path": "b.py", "score": 0.8},
    ]
    out = _mmr_path_diverse(hits, top_k=2)
    assert [h["path"] for h in out] == ["a.py", "b.py"]


def test_sprint_context_drops_low_dense_score(monkeypatch):
    initialize()
    reset_workflow_settings()
    from backend.services import workflow_settings as ws_mod

    monkeypatch.setattr(
        ws_mod,
        "get_workflow_settings",
        lambda *a, **k: {
            **DEFAULT_WORKFLOW_SETTINGS,
            "enableSemanticSearch": True,
            "enableSemanticSprintContext": True,
            "semanticMinScore": 0.8,
            "semanticSprintTopK": 5,
            "enableHybridSearch": False,
        },
    )

    engine = MagicMock()
    engine.index_status.return_value = {"chunks": 10}
    engine.search.return_value = [
        {
            "path": "weak.py",
            "startLine": 1,
            "endLine": 2,
            "content": "x",
            "score": 0.2,
            "source": "dense",
            "denseScore": 0.2,
        },
        {
            "path": "strong.py",
            "startLine": 1,
            "endLine": 2,
            "content": "y",
            "score": 0.95,
            "source": "dense",
            "denseScore": 0.95,
        },
    ]
    with patch("backend.storage.code_index.CodeIndexEngine", return_value=engine):
        block, paths = build_semantic_sprint_context(
            {"title": "T", "description": "d"}, max_chars=4000
        )
    assert "strong.py" in block
    assert "weak.py" not in block
    assert paths == ["strong.py"]


def test_incremental_skips_unchanged_hash(monkeypatch, tmp_path):
    initialize()
    from backend import state

    ws = tmp_path / "proj"
    (ws / "lib").mkdir(parents=True)
    (ws / "lib" / "a.py").write_text("print(1)\n", encoding="utf-8")
    monkeypatch.setattr(state, "WORKSPACE_DIR", str(ws))

    client = MagicMock()
    client.get_collections.return_value.collections = []
    client.scroll.return_value = ([], None)
    client.get_collection.return_value.points_count = 1
    client.get_collection.return_value.config.params.vectors.size = 8

    emb = [0.1] * 8
    with patch.object(CodeIndexEngine, "_verify_embed_model", return_value=None), patch.object(
        CodeIndexEngine, "_embed", return_value=emb
    ), patch.object(CodeIndexEngine, "_get_client", return_value=client):
        engine = CodeIndexEngine()
        engine._embed_dim = 8
        # First index
        r1 = engine.index_workspace(force=True)
        assert r1.get("ok") is True
        # Pretend hashes match for second pass
        with patch.object(
            CodeIndexEngine,
            "_payload_hashes",
            return_value={"lib/a.py": __import__("backend.storage.code_index", fromlist=["_content_hash"])._content_hash("print(1)\n")},
        ):
            r2 = engine.index_workspace(force=False)
    assert r2.get("skippedUnchanged", 0) >= 1


def test_prune_creates_episode_summary():
    initialize()
    reset_workflow_settings()
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "user"},
    ]
    # Pad with huge tool messages
    for i in range(20):
        messages.append(
            {
                "role": "tool",
                "tool_name": "read_file",
                "content": ("x" * 2000) + f" file{i}",
            }
        )
    with patch(
        "backend.services.llm_context.message_prune_threshold_chars",
        return_value=5000,
    ), patch(
        "backend.services.prompt_budget.resolve_ollama_num_ctx",
        return_value=8192,
    ):
        prune_messages_if_needed(messages)
    assert any(
        str(m.get("content") or "").startswith("=== EPISODE SUMMARY ===") for m in messages
    )


def test_observation_and_reflect_markers_in_scrum_agent():
    root = Path(__file__).resolve().parents[1]
    src = (root / "backend" / "agents" / "scrum_agent.py").read_text(encoding="utf-8")
    assert "=== OBSERVATION ===" in src
    assert "=== REFLECT ===" in src
    assert "_save_step_lesson" in src
    assert "=== PERCEIVE (after write) ===" in src


def test_fix_verify_observe_marker():
    root = Path(__file__).resolve().parents[1]
    src = (root / "backend" / "services" / "fix_verify_loop.py").read_text(encoding="utf-8")
    assert "=== OBSERVE (fix-verify round" in src


def test_training_export_row_keys():
    row = step_trace_to_export_row(
        {
            "taskId": "T1",
            "taskTitle": "Hi",
            "agent": "Developer",
            "exitReason": "max_iterations",
            "toolsUsed": ["read_file"],
            "agentResultSnippet": "done",
            "events": [{"type": "tool", "detail": "read_file ok"}],
        }
    )
    for key in ("taskId", "stopReason", "tools", "messages", "outcome"):
        assert key in row
    out = export_training_jsonl(limit=5)
    assert out.get("ok") is True
    assert "jsonl" in out
    assert "offline fine-tuning" in out.get("note", "").lower() or "Export" in out.get("note", "")


def test_defaults_and_ui_markers_rag_loop():
    assert DEFAULT_WORKFLOW_SETTINGS.get("enableHybridSearch") is True
    assert DEFAULT_WORKFLOW_SETTINGS.get("enableStepLessonMemory") is True
    root = Path(__file__).resolve().parents[1]
    panel = (root / "frontend" / "src" / "components" / "WorkflowPanel.tsx").read_text(
        encoding="utf-8"
    )
    assert "enableHybridSearch" in panel
    assert "enableObservationSummaries" in panel
    assert "/api/training/export" in panel
    readme = (root / "README.md").read_text(encoding="utf-8")
    assert "enableHybridSearch" in readme
    assert "enableStepLessonMemory" in readme


def test_save_step_lesson_structured():
    initialize()
    from backend.storage.memory_engine import create_memory_engine

    eng = create_memory_engine()
    eng.save_step_lesson(
        "Developer",
        lesson="Fixed lint via apply_patch",
        stop_reason="completed_with_writes",
        tools_used=["apply_patch"],
        task_id="T-L",
        files=["a.py"],
    )
    hits = eng.search("Developer", "lint apply_patch", limit=5, include_all_agents=True)
    assert hits
    assert "Fixed lint" in hits[0]["content"] or "step_lesson" in hits[0]["content"]
