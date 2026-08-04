from backend.services.llm_context import prepare_tool_output_parts
from backend.services.tool_output_chunks import chunk_tool_output


def test_glob_listing_not_truncated_at_old_6k_cap():
    lines = [f"lib/src/file_{i}.dart" for i in range(400)]
    body = "Glob '*.dart' (400 files):\n" + "\n".join(f"- {p}" for p in lines)
    parts = prepare_tool_output_parts("glob_file_search", body)
    joined = "\n".join(parts)
    assert "file_399.dart" in joined
    assert "file_0.dart" in joined


def test_chunk_splits_very_large_listing():
    cap = 5000
    body = "\n".join(f"line-{i}.dart" for i in range(800))
    parts = chunk_tool_output("glob_file_search", body, cap)
    assert len(parts) >= 2
    assert "part 1/" in parts[0]
    assert "line-799.dart" in parts[-1]
