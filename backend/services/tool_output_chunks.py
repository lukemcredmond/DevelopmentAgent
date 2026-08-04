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

        if len(chunks) <= 1:
            chunks = [body[: cap - 80] + f"\n...[truncated at {cap} chars]\n"]

        total = len(chunks)
        if total == 1:
            return chunks
        labeled: List[str] = []
        for i, part in enumerate(chunks, 1):
            labeled.append(
                f"=== {tool_name} output (part {i}/{total}) ===\n"
                f"{part}\n"
                f"[End part {i}/{total} — all parts are consecutive; do not re-run the tool to see this data.]"
            )
        return labeled

    head = max(200, cap // 2)
    tail = max(100, cap - head - 120)
    if head + tail >= len(body):
        return [body[: cap - 60] + f"\n...[truncated at {cap} chars]\n"]
    return [
        body[:head]
        + f"\n...[middle omitted — {len(body) - head - tail} chars]\n"
        + body[-tail:]
    ]
