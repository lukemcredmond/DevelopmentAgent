"""Extensible tool call normalization layer."""

from backend.services.tool_call_normalizer.canonical import (
    MAX_RECOVERED_TOOL_CALLS,
    CanonicalToolCall,
)
from backend.services.tool_call_normalizer.native import (
    canonical_to_provider_tool_calls,
    normalize_anthropic_message,
    normalize_ollama_message,
    normalize_openai_message,
)
from backend.services.tool_call_normalizer.normalize import (
    normalize_assistant_message,
    normalize_existing_tool_call_arguments,
)
from backend.services.tool_call_normalizer.registry import parse_text_tool_calls
from backend.services.tool_call_normalizer.utils import (
    looks_like_raw_tool_markup,
    normalize_tool_arguments,
    unwrap_llm_text,
)

__all__ = [
    "MAX_RECOVERED_TOOL_CALLS",
    "CanonicalToolCall",
    "canonical_to_provider_tool_calls",
    "looks_like_raw_tool_markup",
    "normalize_anthropic_message",
    "normalize_assistant_message",
    "normalize_existing_tool_call_arguments",
    "normalize_ollama_message",
    "normalize_openai_message",
    "normalize_tool_arguments",
    "parse_text_tool_calls",
    "unwrap_llm_text",
]
