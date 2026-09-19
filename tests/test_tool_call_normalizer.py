"""Fixture-driven tests for tool_call_normalizer."""

from __future__ import annotations

import json

import pytest

from backend.services.llm_provider import ProviderMessage, ProviderToolCall, ToolFunction
from backend.services.tool_call_normalizer import (
    looks_like_raw_tool_markup,
    normalize_anthropic_message,
    normalize_assistant_message,
    normalize_openai_message,
    parse_text_tool_calls,
)
from backend.services.llm_tool_recovery import (
    apply_tool_call_recovery,
    recover_tool_calls_from_content,
)


ALLOWED = {"read_file", "list_dir", "apply_patch", "run_test"}


def _names(calls) -> list[str]:
    return [c.name for c in calls] if calls and hasattr(calls[0], "name") else [c.function.name for c in calls]


class TestHermesParser:
    def test_single_block(self):
        text = '<tool_call>{"name": "read_file", "arguments": {"path": "main.dart"}}</tool_call>'
        calls, source = parse_text_tool_calls(text, ALLOWED)
        assert source == "hermes_xml"
        assert len(calls) == 1
        assert calls[0].name == "read_file"
        assert calls[0].arguments["path"] == "main.dart"

    def test_multi_block(self):
        text = (
            '<tool_call>{"name": "read_file", "arguments": {"path": "a"}}</tool_call>\n'
            '<tool_call>{"name": "list_dir", "arguments": {"path": "."}}</tool_call>'
        )
        calls, _ = parse_text_tool_calls(text, ALLOWED)
        assert _names(calls) == ["read_file", "list_dir"]

    def test_array_in_one_tag(self):
        text = (
            "<tool_call>"
            '[{"name": "read_file", "arguments": {"path": "a"}}, '
            '{"name": "list_dir", "arguments": {"path": "."}}]'
            "</tool_call>"
        )
        calls, _ = parse_text_tool_calls(text, ALLOWED)
        assert _names(calls) == ["read_file", "list_dir"]

    def test_parameters_alias(self):
        text = '<tool_call>{"name": "read_file", "parameters": {"path": "a"}}</tool_call>'
        calls, _ = parse_text_tool_calls(text, ALLOWED)
        assert calls[0].arguments["path"] == "a"


class TestQwenXmlParser:
    def test_list_dir(self):
        text = (
            "<tool_call>\n<function=list_dir>\n"
            "<parameter=path>\n.\n</parameter>\n"
            "<parameter=depth>\n2\n</parameter>\n"
            "</function>\n</tool_call>"
        )
        calls, source = parse_text_tool_calls(text, ALLOWED)
        assert source == "qwen_xml"
        assert calls[0].name == "list_dir"
        assert calls[0].arguments["path"] == "."
        assert calls[0].arguments["depth"] == "2"


class TestInvokeXmlParser:
    def test_invoke_parameter_name(self):
        text = '<invoke name="read_file"><parameter name="path">lib/main.dart</parameter></invoke>'
        calls, source = parse_text_tool_calls(text, ALLOWED)
        assert source == "invoke_xml"
        assert calls[0].name == "read_file"
        assert calls[0].arguments["path"] == "lib/main.dart"


class TestMistralParser:
    def test_tool_calls_prefix(self):
        text = '[TOOL_CALLS] [{"name": "read_file", "arguments": {"path": "x.py"}}]'
        calls, source = parse_text_tool_calls(text, ALLOWED)
        assert source == "mistral"
        assert calls[0].name == "read_file"


class TestMarkdownParser:
    def test_bold_fence(self):
        text = "**run_test**\n```json\n{}\n```"
        calls, source = parse_text_tool_calls(text, ALLOWED)
        assert source == "markdown"
        assert calls[0].name == "run_test"


class TestBareJsonParser:
    def test_openai_shape(self):
        payload = {"function": {"name": "apply_patch", "arguments": {"path": "x.dart"}}}
        calls, source = parse_text_tool_calls(json.dumps(payload), ALLOWED)
        assert source == "json"
        assert calls[0].name == "apply_patch"

    def test_shorthand(self):
        payload = {"read_file": {"path": "a.dart"}}
        calls, _ = parse_text_tool_calls(json.dumps(payload), ALLOWED)
        assert calls[0].name == "read_file"

    def test_legacy_function_call_in_content(self):
        payload = {"function_call": {"name": "read_file", "arguments": '{"path": "x"}'}}
        calls, _ = parse_text_tool_calls(json.dumps(payload), ALLOWED)
        assert calls[0].name == "read_file"
        assert calls[0].arguments["path"] == "x"


class TestNativeAdapters:
    def test_openai_tool_calls(self):
        msg = {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": "call_abc",
                    "type": "function",
                    "function": {"name": "read_file", "arguments": '{"path": "a"}'},
                }
            ],
        }
        calls = normalize_openai_message(msg)
        assert len(calls) == 1
        assert calls[0].name == "read_file"
        assert calls[0].arguments["path"] == "a"

    def test_openai_legacy_function_call(self):
        msg = {
            "role": "assistant",
            "function_call": {"name": "read_file", "arguments": '{"path": "b"}'},
        }
        calls = normalize_openai_message(msg)
        assert calls[0].name == "read_file"
        assert calls[0].arguments["path"] == "b"

    def test_anthropic_tool_use(self):
        msg = {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "I'll read the file."},
                {
                    "type": "tool_use",
                    "id": "tu_01",
                    "name": "read_file",
                    "input": {"path": "main.dart"},
                },
            ],
        }
        calls = normalize_anthropic_message(msg)
        assert len(calls) == 1
        assert calls[0].id == "tu_01"
        assert calls[0].arguments["path"] == "main.dart"


class TestNormalizeAssistantMessage:
    def test_thinking_only_recovery(self):
        msg = ProviderMessage(
            role="assistant",
            content="",
            thinking='<tool_call>{"name": "read_file", "arguments": {"path": "x"}}</tool_call>',
        )
        updated, names, source = normalize_assistant_message(msg, ALLOWED)
        assert names == ["read_file"]
        assert source == "hermes_xml"
        assert updated.tool_calls is not None
        assert isinstance(updated.tool_calls[0], ProviderToolCall)
        assert updated.content == ""

    def test_provider_tool_call_output_type(self):
        msg = ProviderMessage(
            role="assistant",
            content='{"name": "read_file", "arguments": {"path": "lib/a.dart"}}',
        )
        updated, names, _ = normalize_assistant_message(msg, ALLOWED)
        assert names == ["read_file"]
        assert isinstance(updated.tool_calls[0], ProviderToolCall)
        assert isinstance(updated.tool_calls[0].function, ToolFunction)


class TestLooksLikeRawToolMarkup:
    def test_detects_mistral_and_invoke(self):
        assert looks_like_raw_tool_markup("[TOOL_CALLS] []")
        assert looks_like_raw_tool_markup('<invoke name="read_file">')
        assert not looks_like_raw_tool_markup("## Summary\nA plan")


class TestApplyToolCallRecoveryCompat:
    def test_recover_from_content(self):
        payload = {"name": "read_file", "arguments": {"path": "lib/main.dart"}}
        msg = ProviderMessage(role="assistant", content=json.dumps(payload))
        names, updated = apply_tool_call_recovery(msg, ALLOWED)
        assert names == ["read_file"]
        assert updated.tool_calls is not None

    @pytest.mark.skipif(
        __import__("importlib").util.find_spec("ollama") is None,
        reason="ollama not installed",
    )
    def test_ollama_message_validation(self):
        from ollama._types import Message as OllamaMessage

        payload = {"name": "read_file", "arguments": {"path": "lib/main.dart"}}
        msg = OllamaMessage(role="assistant", content=json.dumps(payload))
        names, updated = apply_tool_call_recovery(msg, ALLOWED)
        assert names == ["read_file"]
        OllamaMessage.model_validate(updated.model_dump())


class TestRecoverToolCallsFromContentCompat:
    def test_existing_tests_compat(self):
        text = "**run_test**\n```json\n{}\n```"
        calls = recover_tool_calls_from_content(text, ALLOWED)
        assert len(calls) == 1
        assert calls[0].function.name == "run_test"


class TestToolJsonRecovery:
    def test_detect_invalid_json_error(self):
        from backend.services.tool_json_recovery import (
            default_arguments_for_tool_recovery,
            is_invalid_tool_json_error,
            parse_tool_name_from_invalid_json_error,
            synthetic_tool_call_chat_result,
        )

        err = (
            'llama-server returned invalid tool call arguments for "list_dir": '
            "unexpected end of JSON input (status code: 500)"
        )
        assert is_invalid_tool_json_error(err)
        assert parse_tool_name_from_invalid_json_error(err) == "list_dir"
        assert default_arguments_for_tool_recovery("list_dir") == {}
        result = synthetic_tool_call_chat_result("list_dir", {})
        assert result.message.tool_calls[0].function.name == "list_dir"
        assert result.message.tool_calls[0].function.arguments == {}
