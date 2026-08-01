"""Tests for fenced/quoted tool recovery from LLM content."""

from __future__ import annotations

import json

from backend.services.llm_tool_recovery import (
    normalize_tool_arguments,
    recover_tool_calls_from_content,
    unwrap_llm_text,
)


def test_unwrap_llm_text_triple_single_quotes():
    inner = '{"name": "read_file", "arguments": {"path": "a.dart"}}'
    assert unwrap_llm_text(f"'''{inner}'''") == inner


def test_unwrap_llm_text_triple_double_quotes():
    inner = '{"name": "read_file", "arguments": {"path": "b.dart"}}'
    assert unwrap_llm_text(f'"""{inner}"""') == inner


def test_unwrap_llm_text_json_fence():
    inner = '{"name": "read_file", "arguments": {"path": "c.dart"}}'
    assert unwrap_llm_text(f"```json\n{inner}\n```") == inner


def test_normalize_tool_arguments_quoted_json():
    args = normalize_tool_arguments('\'\'\'{"path": "a.dart"}\'\'\'')
    assert args == {"path": "a.dart"}


def test_recover_read_file_from_triple_quotes():
    payload = {"name": "read_file", "arguments": {"path": "lib/main.dart"}}
    text = f"'''{json.dumps(payload)}'''"
    calls = recover_tool_calls_from_content(text, {"read_file", "apply_patch"})
    assert len(calls) == 1
    assert calls[0].function.name == "read_file"
    assert calls[0].function.arguments == {"path": "lib/main.dart"}


def test_recover_apply_patch_openai_shape():
    payload = {
        "function": {
            "name": "apply_patch",
            "arguments": {
                "path": "x.dart",
                "old_text": "a",
                "new_text": "b",
            },
        }
    }
    text = f"```json\n{json.dumps(payload)}\n```"
    calls = recover_tool_calls_from_content(text, {"apply_patch"})
    assert len(calls) == 1
    assert calls[0].function.name == "apply_patch"
    assert calls[0].function.arguments["path"] == "x.dart"


def test_recover_rejects_unknown_tool():
    payload = {"name": "rm_rf_everything", "arguments": {}}
    calls = recover_tool_calls_from_content(json.dumps(payload), {"read_file"})
    assert calls == []


def test_recover_shorthand_single_tool_key():
    payload = {"read_file": {"path": "pubspec.yaml"}}
    calls = recover_tool_calls_from_content(json.dumps(payload), {"read_file"})
    assert len(calls) == 1
    assert calls[0].function.arguments == {"path": "pubspec.yaml"}
