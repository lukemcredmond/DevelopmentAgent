"""Unit tests for tool-output echo detection."""

from backend.services.llm_echo_guard import (
    detect_tool_output_echo,
    normalize_for_compare,
)


def _tool_msg(name: str, body: str) -> dict:
    return {"role": "tool", "name": name, "content": body}


def test_normalize_strips_tool_response_tags():
    raw = "<tool_response>\nline one\n</tool_response>"
    assert "line one" in normalize_for_compare(raw)
    assert "<tool_response" not in normalize_for_compare(raw).lower()


def test_detect_echo_wrapped_read_file_body():
    tool_body = "FILE: src/example.py\n" + ("x" * 120)
    messages = [
        {"role": "assistant", "content": "reading"},
        _tool_msg("read_file", tool_body),
    ]
    assistant = f"<tool_response>\n{tool_body}\n</tool_response>"
    hit = detect_tool_output_echo(assistant, messages)
    assert hit.is_echo
    assert hit.tool_name == "read_file"


def test_detect_echo_partial_overlap():
    tool_body = "BEGIN " + ("alpha " * 40)
    messages = [_tool_msg("read_file", tool_body)]
    assistant = tool_body[: int(len(tool_body) * 0.9)]
    hit = detect_tool_output_echo(assistant, messages)
    assert hit.is_echo
    assert hit.similarity >= 0.85


def test_short_ok_reply_not_echo():
    tool_body = "FILE: big\n" + ("y" * 200)
    messages = [_tool_msg("read_file", tool_body)]
    hit = detect_tool_output_echo("OK, applying patch next.", messages)
    assert not hit.is_echo


def test_no_substantive_tool_tag_only_short_not_echo():
    messages = [{"role": "user", "content": "go"}]
    body = "<tool_response>done</tool_response>"
    hit = detect_tool_output_echo(body, messages)
    assert not hit.is_echo
