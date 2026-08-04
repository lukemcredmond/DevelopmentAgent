"""Split large tool outputs into multiple LLM messages (line-aware listings)."""

from __future__ import annotations

from typing import List

_LINE_CHUNK_TOOLS = frozenset(
    {
        "glob_file_search",
        "list_dir",
        "grep",
        "read_file",
        "search_code",
        "semantic_search",
        "run_command",
    }
)


def _label_parts(tool_name: str, parts: List[str]) -> List[str]:
    total = len(parts)
    if total <= 1:
        return parts
    labeled: List[str] = []
    for i, part in enumerate(parts, 1):
        labeled.append(
            f"=== {tool_name} output (part {i}/{total}) ===\n"
            f"{part}\n"
            f"[End part {i}/{total} — all parts are consecutive; do not re-run the tool to see this data.]"
        )
    return labeled


def _chunk_by_char_windows(tool_name: str, body: str, cap: int) -> List[str]:
    """Split into fixed windows so 100% of body reaches the LLM (no head/tail drop)."""
    if len(body) <= cap:
        return [body]
    windows: List[str] = []
    step = max(400, cap - 96)
    for start in range(0, len(body), step):
        windows.append(body[start : start + step])
    return _label_parts(tool_name, windows)


def chunk_tool_output(tool_name: str, text: str, cap: int) -> List[str]:
    """Return one or more parts; only split when text exceeds cap."""
    body = str(text or "")
    if len(body) <= cap:
        return [body]

    if tool_name in _LINE_CHUNK_TOOLS and "\n" in body:
        lines = body.splitlines()
        chunks: List[str] = []
        buf: List[str] = []
        buf_len = 0
        header_reserve = 96
        chunk_budget = max(400, cap - header_reserve)

        def flush(part_index: int) -> None:
            nonlocal buf, buf_len
            if not buf:
                return
            chunks.append("\n".join(buf))
            buf = []
            buf_len = 0

        for line in lines:
            add = len(line) + 1
            if buf and buf_len + add > chunk_budget:
                flush(len(chunks) + 1)
            buf.append(line)
            buf_len += add
        flush(len(chunks) + 1)

        if len(chunks) <= 1 and len(body) > cap:
            return _chunk_by_char_windows(tool_name, body, cap)

        total = len(chunks)
        if total == 1:
            return chunks
        return _label_parts(tool_name, chunks)

    return _chunk_by_char_windows(tool_name, body, cap)
