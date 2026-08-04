"""Sprint context sources snapshot for UI."""

from backend.services.sprint_context_sources import build_context_sources_snapshot


def test_build_context_sources_snapshot_fields():
    snap = build_context_sources_snapshot(
        task_id="T-1",
        agent_role="Developer",
        local_slm=False,
        semantic_paths=["a.py", "b.py"],
        file_paths=["a.py"],
        graph_used=True,
        pack_mode="repomix",
        codebase_pack_chars=9000,
    )
    assert snap["taskId"] == "T-1"
    assert snap["semanticUsed"] is True
    assert snap["semanticChunkCount"] == 2
    assert snap["contextPacker"] == "repomix"
    assert snap["contextPackerChars"] == 9000
    assert snap["graphUsed"] is True
