"""Tools safe to execute concurrently within a single model turn."""

from __future__ import annotations

PARALLEL_SAFE_TOOLS = frozenset(
    {
        "read_file",
        "grep",
        "glob_file_search",
        "list_dir",
        "git_diff",
        "git_status",
        "search_code",
        "semantic_search",
    }
)


def is_parallel_safe(tool_name: str) -> bool:
    return tool_name in PARALLEL_SAFE_TOOLS


def partition_tool_calls(calls: list) -> tuple[list, list]:
    """Split tool calls into parallel-safe batch and sequential remainder (stable order)."""
    parallel: list = []
    sequential: list = []
    for call in calls:
        name = call.function.name if hasattr(call, "function") else str(call.get("name", ""))
        if is_parallel_safe(name):
            parallel.append(call)
        else:
            sequential.append(call)
    return parallel, sequential
