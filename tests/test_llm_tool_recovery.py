"""Tests for fenced/quoted tool recovery from LLM content."""

from __future__ import annotations

import json

from backend.services.llm_tool_recovery import (
    assistant_message_to_chat_dict,
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


def test_recover_run_test_bold_json_fence():
    text = "**run_test**\n```json\n{}\n```"
    calls = recover_tool_calls_from_content(text, {"run_test", "read_file"})
    assert len(calls) == 1
    assert calls[0].function.name == "run_test"
    assert calls[0].function.arguments == {}


def test_recover_run_test_bold_adjacent_fence():
    text = "**run_test**```json\n{}\n```"
    calls = recover_tool_calls_from_content(text, {"run_test"})
    assert len(calls) == 1
    assert calls[0].function.name == "run_test"


def test_recover_prose_then_bold_tool():
    text = "I'll verify now.\n**run_test**\n```json\n{}\n```"
    calls = recover_tool_calls_from_content(text, {"run_test"})
    assert len(calls) == 1


def test_recover_qwen_xml_list_dir():
    text = (
        "I'll start by exploring the existing codebase.\n\n"
        "<tool_call>\n"
        "<function=list_dir>\n"
        "<parameter=depth>\n"
        "2\n"
        "</parameter>\n"
        "<parameter=path>\n"
        ".\n"
        "</parameter>\n"
        "</function>\n"
        "</tool_call>\n"
    )
    calls = recover_tool_calls_from_content(text, {"list_dir", "read_file"})
    assert len(calls) == 1
    assert calls[0].function.name == "list_dir"
    assert calls[0].function.arguments["path"] == "."
    assert calls[0].function.arguments["depth"] == "2"


def test_looks_like_raw_tool_markup():
    from backend.services.llm_tool_recovery import looks_like_raw_tool_markup

    assert looks_like_raw_tool_markup("<function=list_dir><parameter=path>.</parameter></function>")
    assert not looks_like_raw_tool_markup("## Summary\nA plan")


def test_apply_recovery_produces_valid_ollama_message():
    import importlib.util

    if importlib.util.find_spec("ollama") is None:
        return
    from ollama._types import Message as OllamaMessage

    from backend.services.llm_tool_recovery import apply_tool_call_recovery

    payload = {"name": "read_file", "arguments": {"path": "lib/main.dart"}}
    text = f"'''{json.dumps(payload)}'''"
    msg = OllamaMessage(role="assistant", content=text)
    names, updated = apply_tool_call_recovery(msg, {"read_file", "apply_patch"})
    assert names == ["read_file"]
    OllamaMessage.model_validate(updated.model_dump())
    hist = assistant_message_to_chat_dict(updated)
    assert hist["tool_calls"][0]["function"]["name"] == "read_file"


def test_recover_hermes_multi_block():
    text = (
        '<tool_call>{"name": "read_file", "arguments": {"path": "a"}}</tool_call>\n'
        '<tool_call>{"name": "list_dir", "arguments": {"path": "."}}</tool_call>'
    )
    calls = recover_tool_calls_from_content(text, {"read_file", "list_dir"})
    assert len(calls) == 2
    assert calls[0].function.name == "read_file"
    assert calls[1].function.name == "list_dir"


def test_recover_from_thinking_via_normalize():
    from backend.services.llm_provider import ProviderMessage
    from backend.services.tool_call_normalizer import normalize_assistant_message

    msg = ProviderMessage(
        role="assistant",
        content="",
        thinking='<tool_call>{"name": "read_file", "arguments": {"path": "x"}}</tool_call>',
    )
    updated, names, source = normalize_assistant_message(msg, {"read_file"})
    assert names == ["read_file"]
    assert source == "hermes_xml"
    assert updated.tool_calls is not None


def test_looks_like_raw_tool_markup_mistral():
    from backend.services.llm_tool_recovery import looks_like_raw_tool_markup

    assert looks_like_raw_tool_markup("[TOOL_CALLS] [{\"name\": \"read_file\"}]")
