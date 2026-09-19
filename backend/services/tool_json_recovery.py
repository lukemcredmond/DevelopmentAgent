"""Recover from llama-server errors when tool-call JSON is truncated or empty."""

from __future__ import annotations

import re
from typing import Any, Dict, Optional

from backend.services.llm_provider import ChatResult, ProviderMessage, ProviderToolCall, ToolFunction

_INVALID_TOOL_JSON_RE = re.compile(
    r'invalid tool call arguments for "([^"]+)"',
    re.IGNORECASE,
)

_DEFAULT_TOOL_ARGS: Dict[str, Dict[str, Any]] = {
    "list_dir": {},
    "glob_file_search": {"pattern": "**/*", "limit": 100},
    "grep": {"pattern": ".", "head_limit": 20},
    "read_file": {"path": "README.md"},
}
# PO board tools (add_backlog_tasks, update_board, add_subtasks) are intentionally
# omitted — their schemas are large and truncated JSON cannot be safely defaulted.


def is_invalid_tool_json_error(error: str) -> bool:
    lower = str(error or "").lower()
    return "invalid tool call arguments" in lower and "unexpected end of json" in lower


def parse_tool_name_from_invalid_json_error(error: str) -> Optional[str]:
    match = _INVALID_TOOL_JSON_RE.search(str(error or ""))
    return match.group(1).strip() if match else None


def default_arguments_for_tool_recovery(tool_name: str) -> Optional[Dict[str, Any]]:
    name = str(tool_name or "").strip()
    if not name:
        return None
    defaults = _DEFAULT_TOOL_ARGS.get(name)
    if defaults is None:
        return None
    return dict(defaults)


def synthetic_tool_call_chat_result(tool_name: str, arguments: Dict[str, Any]) -> ChatResult:
    """Build a minimal ChatResult the agent loop can execute like a native tool call."""
    return ChatResult(
        message=ProviderMessage(
            role="assistant",
            content=None,
            tool_calls=[
                ProviderToolCall(
                    id="recovered_tool_json_0",
                    function=ToolFunction(name=str(tool_name), arguments=dict(arguments)),
                )
            ],
        ),
        prompt_eval_count=0,
        eval_count=0,
        raw=None,
    )
