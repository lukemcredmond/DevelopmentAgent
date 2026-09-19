"""Text-based tool call parsers."""

from backend.services.tool_call_normalizer.parsers.bare_json import BareJsonParser
from backend.services.tool_call_normalizer.parsers.hermes import HermesXmlJsonParser
from backend.services.tool_call_normalizer.parsers.invoke_xml import InvokeXmlParser
from backend.services.tool_call_normalizer.parsers.markdown import MarkdownFenceParser
from backend.services.tool_call_normalizer.parsers.mistral import MistralPrefixParser
from backend.services.tool_call_normalizer.parsers.qwen_xml import QwenParameterXmlParser

DEFAULT_PARSERS = [
    HermesXmlJsonParser(),
    QwenParameterXmlParser(),
    InvokeXmlParser(),
    MistralPrefixParser(),
    MarkdownFenceParser(),
    BareJsonParser(),
]

__all__ = [
    "BareJsonParser",
    "HermesXmlJsonParser",
    "InvokeXmlParser",
    "MarkdownFenceParser",
    "MistralPrefixParser",
    "QwenParameterXmlParser",
    "DEFAULT_PARSERS",
]
