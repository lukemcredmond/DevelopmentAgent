"""Shared tool execution path for sprint agents, manual test runs, and transcript replay."""

from __future__ import annotations

import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Iterator, Literal, Optional

from backend import state
from backend.agents.agent_run import append_recent_tool, get_active_run
from backend.agents.task_context import (
    publish_activity,
    record_task_decision,
    record_task_file,
    record_task_transcript,
    sync_task_files_from_transcript,
    find_task_by_id,
)
from backend.agents.tool_outcomes import (
    file_action_for_tool,
    file_path_from_tool,
    format_tool_transcript_content,
    is_tool_failure,
    parse_run_command_exit,
    run_command_status_label,
    sanitize_tool_args_for_log,
    summarize_tool_args,
)
from backend.services.events import publish_event
from backend.services.logs import add_system_log
from backend.services.tool_approval import request_tool_approval, tool_requires_approval

ToolSource = Literal["agent", "manual", "replay"]


def _get_agent_map():
    from backend.agents.registry import AGENT_MAP

    return AGENT_MAP


def _get_agent_labels():
    from backend.agents.registry import AGENT_LABELS

    return AGENT_LABELS


AGENT_ID_BY_ROLE: dict[str, str] = {}


def _agent_id_by_role() -> dict[str, str]:
    global AGENT_ID_BY_ROLE
    if not AGENT_ID_BY_ROLE:
        AGENT_ID_BY_ROLE = {agent.role: agent_id for agent_id, agent in _get_agent_map().items()}
    return AGENT_ID_BY_ROLE


@dataclass
class ToolExecutionResult:
    tool_name: str
    arguments: Dict[str, Any]
    safe_args: Dict[str, Any]
    tool_output: str
    success: bool
    duration_ms: int
    timestamp: str
    agent: str
    agent_id: str
    task_id: Optional[str]
    source: ToolSource
    run_id: str


@contextmanager
def _sprint_context(task_id: Optional[str], agent_role: str) -> Iterator[None]:
    prev_task = state.ACTIVE_SPRINT_TASK_ID
    prev_agent = state.ACTIVE_SPRINT_AGENT
    if task_id:
        state.ACTIVE_SPRINT_TASK_ID = task_id
    state.ACTIVE_SPRINT_AGENT = agent_role
    try:
        yield
    finally:
        state.ACTIVE_SPRINT_TASK_ID = prev_task
        state.ACTIVE_SPRINT_AGENT = prev_agent


def resolve_agent(agent_id: str):
    agent = _get_agent_map().get(agent_id)
    if not agent:
        raise ValueError(f"Unknown agent id: {agent_id}")
    return agent


def resolve_agent_id(agent_id: Optional[str] = None, agent_role: Optional[str] = None) -> str:
    agent_map = _get_agent_map()
    if agent_id and agent_id in agent_map:
        return agent_id
    id_by_role = _agent_id_by_role()
    if agent_role and agent_role in id_by_role:
        return id_by_role[agent_role]
    if agent_role:
        for aid, label in _get_agent_labels().items():
            if label == agent_role:
                return aid
    raise ValueError(f"Unknown agent: {agent_id or agent_role}")


def list_agent_tools(agent_id: str) -> list[Dict[str, Any]]:
    agent = resolve_agent(agent_id)
    return agent.registry.get_definitions()


def _history_event_from_transcript(
    task_id: str,
    entry: Dict[str, Any],
) -> Dict[str, Any]:
    tool_name = str(entry.get("toolName") or "?")
    tool_output = str(entry.get("toolOutput") or entry.get("content") or "")
    success = entry.get("toolSuccess") is not False
    if entry.get("toolSuccess") is None and entry.get("role") == "tool":
        content = str(entry.get("content") or "")
        success = "✗" not in content and " FAILED " not in content.upper()
    ts = str(entry.get("timestamp") or "")
    source = entry.get("source") or "agent"
    run_id = f"{task_id}-history"
    exit_code = None
    run_cmd_status = None
    if tool_name == "run_command" and tool_output:
        exit_code, _ = parse_run_command_exit(tool_output)
        run_cmd_status = run_command_status_label(tool_output, success)
    return {
        "runId": run_id,
        "taskId": task_id,
        "agent": str(entry.get("agent") or "Agent"),
        "toolName": tool_name,
        "toolArgs": entry.get("toolArgs") or {},
        "toolSuccess": success,
        "toolOutput": tool_output[:500],
        "durationMs": 0,
        "timestamp": ts,
        "source": source,
        "status": "failed" if not success else "completed",
        **(
            {"exitCode": exit_code, "runCommandStatus": run_cmd_status}
            if tool_name == "run_command" and exit_code is not None
            else {}
        ),
    }


def get_tool_history(limit: int = 200) -> list[Dict[str, Any]]:
    """Collect recent tool invocations from task transcripts and the active run."""
    from backend.services.feature_similarity import iter_board_tasks

    events: list[Dict[str, Any]] = []
    for task in iter_board_tasks():
        task_id = str(task.get("id") or "")
        if not task_id:
            continue
        for entry in task.get("transcript") or []:
            if not isinstance(entry, dict) or not entry.get("toolName"):
                continue
            events.append(_history_event_from_transcript(task_id, entry))

    active_run = get_active_run()
    if active_run:
        for rt in active_run.recent_tools:
            ts = str(rt.get("timestamp") or "")
            tool_name = str(rt.get("toolName") or "?")
            success = rt.get("toolSuccess") is not False
            tool_output = str(rt.get("toolOutput") or "")
            events.append(
                {
                    "runId": active_run.run_id,
                    "taskId": active_run.task_id,
                    "agent": active_run.agent,
                    "toolName": tool_name,
                    "toolArgs": {},
                    "toolSuccess": success,
                    "toolOutput": tool_output[:500],
                    "durationMs": int(rt.get("durationMs") or 0),
                    "timestamp": ts,
                    "source": "agent",
                    "status": "failed" if not success else "completed",
                }
            )

    events.sort(key=lambda e: str(e.get("timestamp") or ""), reverse=True)
    return events[:limit]


def get_transcript_tool_entries(task_id: str) -> list[Dict[str, Any]]:
    task = find_task_by_id(task_id)
    if not task:
        return []
    entries: list[Dict[str, Any]] = []
    for index, entry in enumerate(task.get("transcript") or []):
        if not entry.get("toolName"):
            continue
        entries.append(
            {
                "index": index,
                "toolName": entry.get("toolName"),
                "toolArgs": entry.get("toolArgs") or {},
                "toolSuccess": entry.get("toolSuccess"),
                "timestamp": entry.get("timestamp"),
                "source": entry.get("source", "agent"),
                "content": entry.get("content", "")[:500],
            }
        )
    return entries


def _record_tool_side_effects(
    *,
    task_id: str,
    agent_role: str,
    tool_name: str,
    arguments: Dict[str, Any],
    safe_args: Dict[str, Any],
    tool_output: str,
    success: bool,
    source: ToolSource,
    save_memory: bool,
    user_prompt: str,
    memory_engine: Any,
) -> None:
    content = format_tool_transcript_content(
        tool_name, arguments, tool_output, success=success
    )
    record_task_transcript(
        task_id,
        "tool",
        content,
        agent=agent_role,
        toolName=tool_name,
        toolSuccess=success,
        toolArgs=safe_args,
        toolOutput=tool_output[:2000],
        source=source,
    )
    record_task_decision(
        task_id,
        agent_role,
        "tool_fail" if not success else "tool",
        f"{'Failed' if not success else 'Used'} tool '{tool_name}'",
        tool_output[:500],
    )
    if success:
        action = file_action_for_tool(tool_name)
        path = file_path_from_tool(tool_name, arguments)
        if action and path:
            record_task_file(task_id, path, action, persist=True)
    else:
        action = file_action_for_tool(tool_name)
        path = file_path_from_tool(tool_name, arguments)
        if action and path:
            record_task_file(task_id, path, f"{action}-failed", persist=True)

    arg_summary = summarize_tool_args(tool_name, arguments)
    if success:
        if tool_name == "write_file":
            path = safe_args.get("path", "?")
            nbytes = safe_args.get("contentLength", 0)
            add_system_log(agent_role, "success", f"write_file OK {path} ({nbytes} bytes)")
        else:
            add_system_log(agent_role, "success", f"{tool_name} OK — {arg_summary}")
    else:
        add_system_log(
            agent_role,
            "error",
            f"{tool_name} FAILED {arg_summary} — {tool_output[:300]}",
        )

    if save_memory and memory_engine is not None:
        memory_engine.save(
            agent_role,
            f"Invoked tool '{tool_name}' on task: {user_prompt}",
            "tool_usage",
        )

    task = find_task_by_id(task_id)
    if task:
        sync_task_files_from_transcript(task)


def execute_tool(
    agent_id: str,
    tool_name: str,
    arguments: Dict[str, Any],
    *,
    task_id: Optional[str] = None,
    source: ToolSource = "manual",
    skip_approval: bool = False,
    run_id: Optional[str] = None,
    user_prompt: str = "",
    on_awaiting_approval: Optional[Any] = None,
    on_tool_executing: Optional[Any] = None,
) -> ToolExecutionResult:
    """Run a single tool through the agent registry with SSE events and optional transcript recording."""
    agent = resolve_agent(agent_id)
    agent_role = agent.role
    safe_args = sanitize_tool_args_for_log(tool_name, arguments)
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    active_run = get_active_run()
    effective_run_id = run_id or (active_run.run_id if active_run else "manual")

    with _sprint_context(task_id, agent_role):
        arg_summary = summarize_tool_args(tool_name, arguments)
        log_prefix = {"manual": "Manual", "replay": "Replay", "agent": "Calling"}[source]
        add_system_log(agent_role, "info", f"{log_prefix} {tool_name} — {arg_summary}")

        publish_event(
            "tool_start",
            {
                "runId": effective_run_id,
                "taskId": task_id or "system",
                "agent": agent_role,
                "toolName": tool_name,
                "toolArgs": safe_args,
                "timestamp": ts,
                "source": source,
            },
        )
        if on_tool_executing:
            on_tool_executing(tool_name)

        started = time.time()
        tool_output = ""
        success = False

        if not skip_approval and tool_requires_approval(tool_name):
            if on_awaiting_approval:
                on_awaiting_approval(tool_name)
            approved, deny_msg = request_tool_approval(
                effective_run_id,
                tool_name,
                arguments,
                task_id=task_id,
                agent=agent_role,
            )
            if on_tool_executing:
                on_tool_executing(tool_name)
            if not approved:
                tool_output = deny_msg
                success = False
            else:
                tool_output = agent.registry.invoke(tool_name, arguments)
                success = not is_tool_failure(tool_name, tool_output)
        else:
            tool_output = agent.registry.invoke(tool_name, arguments)
            success = not is_tool_failure(tool_name, tool_output)

        duration_ms = int((time.time() - started) * 1000)
        output_preview = tool_output[:500]

        exit_code, _ = parse_run_command_exit(tool_output) if tool_name == "run_command" else (None, None)
        run_cmd_status = (
            run_command_status_label(tool_output, success) if tool_name == "run_command" else None
        )

        tool_entry = {
            "toolName": tool_name,
            "toolSuccess": success,
            "toolOutput": output_preview,
            "durationMs": duration_ms,
            "timestamp": ts,
        }
        if active_run or source in ("manual", "replay"):
            append_recent_tool(tool_entry)

        publish_event(
            "tool_end",
            {
                "runId": effective_run_id,
                "taskId": task_id or "system",
                "agent": agent_role,
                "toolName": tool_name,
                "toolArgs": safe_args,
                "toolSuccess": success,
                "toolOutput": output_preview,
                "durationMs": duration_ms,
                "timestamp": ts,
                "source": source,
                **(
                    {"exitCode": exit_code, "runCommandStatus": run_cmd_status}
                    if tool_name == "run_command"
                    else {}
                ),
            },
        )

        if task_id and source == "agent":
            publish_activity(
                task_id,
                "tool_failed" if not success else "tool_end",
                f"{tool_name} {'FAILED' if not success else 'OK'}: {output_preview[:400]}",
                role="tool",
                agent=agent_role,
            )

        if task_id:
            _record_tool_side_effects(
                task_id=task_id,
                agent_role=agent_role,
                tool_name=tool_name,
                arguments=arguments,
                safe_args=safe_args,
                tool_output=tool_output,
                success=success,
                source=source,
                save_memory=source == "agent",
                user_prompt=user_prompt,
                memory_engine=agent.memory if source == "agent" else None,
            )

    return ToolExecutionResult(
        tool_name=tool_name,
        arguments=arguments,
        safe_args=safe_args,
        tool_output=tool_output,
        success=success,
        duration_ms=duration_ms,
        timestamp=ts,
        agent=agent_role,
        agent_id=agent_id,
        task_id=task_id,
        source=source,
        run_id=effective_run_id,
    )


def replay_transcript_tools(
    task_id: str,
    entry_indices: Optional[list[int]] = None,
    *,
    failed_only: bool = False,
) -> list[ToolExecutionResult]:
    task = find_task_by_id(task_id)
    if not task:
        raise ValueError(f"Task not found: {task_id}")

    transcript = task.get("transcript") or []
    targets: list[tuple[int, Dict[str, Any]]] = []
    for index, entry in enumerate(transcript):
        if not entry.get("toolName"):
            continue
        if entry_indices is not None and index not in entry_indices:
            continue
        if failed_only and entry.get("toolSuccess") is not False:
            continue
        targets.append((index, entry))

    if not targets and entry_indices is None and not failed_only:
        targets = [
            (i, e)
            for i, e in enumerate(transcript)
            if e.get("toolName")
        ]

    results: list[ToolExecutionResult] = []
    agent_role = str(task.get("lastAgent") or "Developer")
    try:
        agent_id = resolve_agent_id(agent_role=agent_role)
    except ValueError:
        agent_id = "dev"

    for _index, entry in targets:
        tool_name = str(entry["toolName"])
        arguments = dict(entry.get("toolArgs") or {})
        result = execute_tool(
            agent_id,
            tool_name,
            arguments,
            task_id=task_id,
            source="replay",
            skip_approval=True,
            user_prompt=f"Replay tool {tool_name}",
        )
        results.append(result)

    return results
