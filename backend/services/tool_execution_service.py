"""Shared tool execution path for sprint agents, manual test runs, and transcript replay."""

from __future__ import annotations

import json
import time
import uuid
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

ToolSource = Literal["agent", "manual", "replay", "orchestrator", "context_inject"]

MAX_TOOL_LOG_ENTRIES = 500
TOOL_OUTPUT_PREVIEW_CHARS = 2000
_RUN_SEQ: dict[str, int] = {}


def _next_seq(run_id: str) -> int:
    seq = _RUN_SEQ.get(run_id, 0) + 1
    _RUN_SEQ[run_id] = seq
    return seq


def _tool_log_setting_key(project_id: str) -> str:
    return f"tool_log:{project_id}"


def _dedupe_key(event: Dict[str, Any]) -> str:
    event_id = event.get("eventId")
    if event_id:
        return str(event_id)
    seq = event.get("seq")
    seq_part = f"|{seq}" if seq is not None else ""
    return "|".join(
        [
            str(event.get("runId") or ""),
            str(event.get("taskId") or ""),
            str(event.get("toolName") or ""),
            str(event.get("timestamp") or ""),
        ]
    ) + seq_part


def _run_command_event_fields(
    tool_name: str,
    arguments: Dict[str, Any],
    tool_output: str,
) -> Dict[str, Any]:
    if tool_name != "run_command":
        return {}
    command = str(arguments.get("command") or "")
    exit_code, body = parse_run_command_exit(tool_output)
    from backend.services.diagnostics_parser import parse_command_diagnostics

    diagnostics = parse_command_diagnostics(command, body or tool_output)
    fields: Dict[str, Any] = {"command": command}
    if diagnostics:
        fields["diagnostics"] = diagnostics[:50]
        fields["diagnosticsCount"] = len(diagnostics)
    if exit_code is not None:
        fields["exitCode"] = exit_code
    return fields


def _build_history_event(
    *,
    run_id: str,
    task_id: str,
    agent: str,
    tool_name: str,
    tool_args: Dict[str, Any],
    tool_output: str,
    success: bool,
    duration_ms: int,
    timestamp: str,
    source: str,
    exit_code: Optional[int] = None,
    run_cmd_status: Optional[str] = None,
    event_id: Optional[str] = None,
    diagnostics: Optional[list] = None,
    command: Optional[str] = None,
    status_override: Optional[str] = None,
) -> Dict[str, Any]:
    event_id = event_id or str(uuid.uuid4())
    event: Dict[str, Any] = {
        "eventId": event_id,
        "seq": _next_seq(run_id),
        "runId": run_id,
        "taskId": task_id,
        "agent": agent,
        "toolName": tool_name,
        "toolArgs": tool_args,
        "toolSuccess": success,
        "toolOutput": tool_output[:TOOL_OUTPUT_PREVIEW_CHARS],
        "durationMs": duration_ms,
        "timestamp": timestamp,
        "source": source,
        "status": status_override or ("failed" if not success else "completed"),
    }
    if tool_name == "run_command":
        if command:
            event["command"] = command
        if exit_code is not None:
            event["exitCode"] = exit_code
        if run_cmd_status is not None:
            event["runCommandStatus"] = run_cmd_status
        if diagnostics:
            event["diagnostics"] = diagnostics[:50]
            event["diagnosticsCount"] = len(diagnostics)
    return event


def persist_tool_log() -> None:
    if not state.CURRENT_PROJECT_ID:
        return
    state.storage.set_setting(
        _tool_log_setting_key(state.CURRENT_PROJECT_ID),
        json.dumps(state.TOOL_EXECUTION_LOG),
    )


def load_tool_log_for_project(project_id: str) -> None:
    raw = state.storage.get_setting(_tool_log_setting_key(project_id))
    state.TOOL_EXECUTION_LOG.clear()
    if not raw:
        return
    try:
        loaded = json.loads(raw)
        if isinstance(loaded, list):
            state.TOOL_EXECUTION_LOG.extend(loaded[-MAX_TOOL_LOG_ENTRIES:])
    except json.JSONDecodeError:
        pass


def append_global_tool_event(event: Dict[str, Any]) -> None:
    with state.STATE_LOCK:
        state.TOOL_EXECUTION_LOG.append(event)
        overflow = len(state.TOOL_EXECUTION_LOG) - MAX_TOOL_LOG_ENTRIES
        if overflow > 0:
            del state.TOOL_EXECUTION_LOG[:overflow]
        persist_tool_log()


def clear_tool_log() -> Dict[str, Any]:
    """Clear persisted global tool execution log for the current project."""
    with state.STATE_LOCK:
        state.TOOL_EXECUTION_LOG.clear()
        persist_tool_log()
    return {"ok": True, "events": []}


def _event_sort_key(timestamp: str) -> str:
    """Sort key for tool events — empty timestamps sort last."""
    ts = (timestamp or "").strip()
    return ts if ts else "0000-00-00 00:00:00"


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
    pending_approval: bool = False
    approval_id: Optional[str] = None


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
    run_id = str(entry.get("runId") or f"{task_id}-tx-{ts}-{tool_name}")
    if tool_name == "run_command" and tool_output:
        exit_code, _ = parse_run_command_exit(tool_output)
        command = str((entry.get("toolArgs") or {}).get("command") or "")
        run_cmd_status = run_command_status_label(tool_output, success, command)
        rc_fields = _run_command_event_fields(tool_name, entry.get("toolArgs") or {}, tool_output)
    else:
        exit_code = None
        run_cmd_status = None
        command = None
        rc_fields = {}
    return {
        "eventId": str(uuid.uuid4()),
        "runId": run_id,
        "taskId": task_id,
        "agent": str(entry.get("agent") or "Agent"),
        "toolName": tool_name,
        "toolArgs": entry.get("toolArgs") or {},
        "toolSuccess": success,
        "toolOutput": tool_output[:TOOL_OUTPUT_PREVIEW_CHARS],
        "durationMs": 0,
        "timestamp": ts,
        "source": source,
        "status": "failed" if not success else "completed",
        **rc_fields,
        **(
            {"exitCode": exit_code, "runCommandStatus": run_cmd_status}
            if tool_name == "run_command" and exit_code is not None
            else {}
        ),
    }


def get_tool_history(limit: int = 200) -> list[Dict[str, Any]]:
    """Collect recent tool invocations from global log and the active run (not task transcripts)."""
    seen: set[str] = set()
    events: list[Dict[str, Any]] = []

    def add_event(ev: Dict[str, Any]) -> None:
        key = _dedupe_key(ev)
        if key in seen:
            return
        seen.add(key)
        events.append(ev)

    with state.STATE_LOCK:
        for ev in reversed(state.TOOL_EXECUTION_LOG):
            add_event(dict(ev))

    active_run = get_active_run()
    if active_run:
        for rt in active_run.recent_tools:
            ts = str(rt.get("timestamp") or "")
            tool_name = str(rt.get("toolName") or "?")
            success = rt.get("toolSuccess") is not False
            tool_output = str(rt.get("toolOutput") or "")
            add_event(
                _build_history_event(
                    run_id=active_run.run_id,
                    task_id=active_run.task_id,
                    agent=active_run.agent,
                    tool_name=tool_name,
                    tool_args={},
                    tool_output=tool_output,
                    success=success,
                    duration_ms=int(rt.get("durationMs") or 0),
                    timestamp=ts,
                    source="agent",
                )
            )

    events.sort(key=lambda e: _event_sort_key(str(e.get("timestamp") or "")), reverse=True)
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
        from backend import state as app_state

        project_id = app_state.CURRENT_PROJECT_ID or "default-proj"
        if success and tool_name in ("apply_patch", "write_file"):
            path = file_path_from_tool(tool_name, arguments) or "?"
            memory_engine.save_outcome(
                agent_role,
                f"{tool_name} {path} succeeded on task {task_id or 'system'}",
                "fix_pattern",
                project_id=project_id,
            )
        elif not success:
            memory_engine.save_outcome(
                agent_role,
                f"{tool_name} failed: {tool_output[:300]}",
                "failure",
                project_id=project_id,
            )

    task = find_task_by_id(task_id)
    if task:
        sync_task_files_from_transcript(task)
        if tool_name == "run_command":
            rc_fields = _run_command_event_fields(tool_name, arguments, tool_output)
            diagnostics = rc_fields.get("diagnostics")
            if diagnostics:
                task["lastCommandDiagnostics"] = diagnostics


def _emit_tool_end(
    *,
    event_id: str,
    effective_run_id: str,
    task_id: Optional[str],
    agent_role: str,
    tool_name: str,
    safe_args: Dict[str, Any],
    arguments: Dict[str, Any],
    tool_output: str,
    success: bool,
    duration_ms: int,
    ts: str,
    source: str,
    active_run: Any,
    status_override: Optional[str] = None,
) -> None:
    """Publish tool_end SSE and persist to global tool log."""
    output_preview = tool_output[:TOOL_OUTPUT_PREVIEW_CHARS]
    exit_code, _ = parse_run_command_exit(tool_output) if tool_name == "run_command" else (None, None)
    run_cmd_status = (
        run_command_status_label(
            tool_output,
            success,
            str(arguments.get("command") or ""),
        )
        if tool_name == "run_command"
        else None
    )
    rc_fields = _run_command_event_fields(tool_name, arguments, tool_output)

    tool_entry = {
        "toolName": tool_name,
        "toolSuccess": success,
        "toolOutput": output_preview,
        "durationMs": duration_ms,
        "timestamp": ts,
    }
    if active_run or source in ("manual", "replay"):
        append_recent_tool(tool_entry)

    end_payload: Dict[str, Any] = {
        "eventId": event_id,
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
        **rc_fields,
    }
    if status_override:
        end_payload["status"] = status_override
    if tool_name == "run_command" and run_cmd_status is not None:
        end_payload["runCommandStatus"] = run_cmd_status

    publish_event("tool_end", end_payload)

    append_global_tool_event(
        _build_history_event(
            run_id=effective_run_id,
            task_id=task_id or "system",
            agent=agent_role,
            tool_name=tool_name,
            tool_args=safe_args,
            tool_output=tool_output,
            success=success,
            duration_ms=duration_ms,
            timestamp=ts,
            source=source,
            exit_code=exit_code,
            run_cmd_status=run_cmd_status,
            event_id=event_id,
            diagnostics=rc_fields.get("diagnostics"),
            command=rc_fields.get("command"),
            status_override=status_override,
        )
    )


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
    event_id = str(uuid.uuid4())

    with _sprint_context(task_id, agent_role):
        arg_summary = summarize_tool_args(tool_name, arguments)
        log_prefix = {"manual": "Manual", "replay": "Replay", "agent": "Calling"}[source]
        add_system_log(agent_role, "info", f"{log_prefix} {tool_name} — {arg_summary}")
        if source == "agent":
            from backend.services.step_diagnostics import get_active_trace, log_event

            if get_active_trace():
                log_event("tool_start", f"{tool_name} — {arg_summary}")

        publish_event(
            "tool_start",
            {
                "eventId": event_id,
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
        from_cache = False

        if source == "agent":
            from backend.services.tool_cache import (
                check_run_command_cache,
                get_cached_result,
                should_cache_tool,
                store_cached_result,
            )

            if should_cache_tool(tool_name, source):
                if tool_name == "run_command":
                    cmd = str(arguments.get("command") or "")
                    cached_cmd = check_run_command_cache(cmd, arguments)
                    if cached_cmd:
                        tool_output = cached_cmd
                        success = not is_tool_failure(tool_name, tool_output)
                        from_cache = True
                else:
                    cached = get_cached_result(tool_name, arguments)
                    if cached:
                        tool_output, success = cached
                        from_cache = True

        if not from_cache:
            if not skip_approval and tool_requires_approval(tool_name, arguments):
                if on_awaiting_approval:
                    on_awaiting_approval(tool_name)
                approved, deny_msg, approval_id = request_tool_approval(
                    effective_run_id,
                    tool_name,
                    arguments,
                    task_id=task_id,
                    agent=agent_role,
                    agent_id=agent_id,
                    user_prompt=user_prompt,
                )
                if deny_msg.startswith("AWAITING_APPROVAL:"):
                    duration_ms = int((time.time() - started) * 1000)
                    _emit_tool_end(
                        event_id=event_id,
                        effective_run_id=effective_run_id,
                        task_id=task_id,
                        agent_role=agent_role,
                        tool_name=tool_name,
                        safe_args=safe_args,
                        arguments=arguments,
                        tool_output=deny_msg,
                        success=False,
                        duration_ms=duration_ms,
                        ts=ts,
                        source=source,
                        active_run=active_run,
                        status_override="awaiting_approval",
                    )
                    return ToolExecutionResult(
                        tool_name=tool_name,
                        arguments=arguments,
                        safe_args=safe_args,
                        tool_output=deny_msg,
                        success=False,
                        duration_ms=duration_ms,
                        timestamp=ts,
                        agent=agent_role,
                        agent_id=agent_id,
                        task_id=task_id,
                        source=source,
                        run_id=effective_run_id,
                        pending_approval=True,
                        approval_id=approval_id,
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

            if source == "agent" and success:
                from backend.services.tool_cache import (
                    should_cache_tool,
                    store_cached_result,
                )

                if should_cache_tool(tool_name, source):
                    store_cached_result(tool_name, arguments, tool_output, success)

        if success:
            if tool_name == "read_file":
                from backend.workspace.files import record_step_file_read

                record_step_file_read(str(arguments.get("path") or ""), tool_output)
            elif tool_name in ("write_file", "apply_patch"):
                from backend.workspace.files import invalidate_step_file_read

                path_key = str(
                    arguments.get("path") or arguments.get("test_script_path") or ""
                )
                if path_key:
                    invalidate_step_file_read(path_key)

        duration_ms = int((time.time() - started) * 1000)
        _emit_tool_end(
            event_id=event_id,
            effective_run_id=effective_run_id,
            task_id=task_id,
            agent_role=agent_role,
            tool_name=tool_name,
            safe_args=safe_args,
            arguments=arguments,
            tool_output=tool_output,
            success=success,
            duration_ms=duration_ms,
            ts=ts,
            source=source,
            active_run=active_run,
        )
        output_preview = tool_output[:TOOL_OUTPUT_PREVIEW_CHARS]

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
        pending_approval=False,
        approval_id=None,
    )


def execute_deferred_approval(approval: Any) -> None:
    """Run a tool after the user approved a non-blocking approval request."""
    from backend.services.tool_approval import PendingToolApproval

    if not isinstance(approval, PendingToolApproval) or approval.executed:
        return
    agent_id = approval.agent_id or "dev"
    execute_tool(
        agent_id,
        approval.tool_name,
        approval.arguments,
        task_id=approval.task_id,
        source="agent",
        skip_approval=True,
        run_id=approval.run_id,
        user_prompt=approval.user_prompt,
    )
    approval.executed = True
    from backend.services.tool_approval import _remove_approval

    _remove_approval(approval)


def log_synthetic_tool_event(
    task_id: str,
    agent_role: str,
    tool_name: str,
    *,
    tool_args: Dict[str, Any],
    tool_output: str,
    success: bool,
    source: Literal["orchestrator", "context_inject"],
    run_id: Optional[str] = None,
) -> None:
    """Record orchestrator/context events in transcript, SSE, and active run (no LLM invocation)."""
    from backend.agents.agent_run import append_recent_tool, get_active_run

    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    active_run = get_active_run()
    effective_run_id = run_id or (active_run.run_id if active_run else "orchestrator")
    output_preview = tool_output[:TOOL_OUTPUT_PREVIEW_CHARS]
    event_id = str(uuid.uuid4())
    rc_fields = _run_command_event_fields(tool_name, tool_args, tool_output)
    command = str(tool_args.get("command") or "") if tool_name == "run_command" else ""
    exit_code = rc_fields.get("exitCode")
    run_cmd_status = None
    if tool_name == "run_command":
        run_cmd_status = run_command_status_label(tool_output, success, command)

    publish_event(
        "tool_start",
        {
            "eventId": event_id,
            "runId": effective_run_id,
            "taskId": task_id,
            "agent": agent_role,
            "toolName": tool_name,
            "toolArgs": tool_args,
            "timestamp": ts,
            "source": source,
        },
    )

    tool_end_payload: Dict[str, Any] = {
        "eventId": event_id,
        "runId": effective_run_id,
        "taskId": task_id,
        "agent": agent_role,
        "toolName": tool_name,
        "toolArgs": tool_args,
        "toolSuccess": success,
        "toolOutput": output_preview,
        "durationMs": 0,
        "timestamp": ts,
        "source": source,
        **rc_fields,
    }
    if tool_name == "run_command" and run_cmd_status is not None:
        tool_end_payload["runCommandStatus"] = run_cmd_status

    publish_event(
        "tool_end",
        tool_end_payload,
    )

    append_global_tool_event(
        _build_history_event(
            run_id=effective_run_id,
            task_id=task_id,
            agent=agent_role,
            tool_name=tool_name,
            tool_args=tool_args,
            tool_output=tool_output,
            success=success,
            duration_ms=0,
            timestamp=ts,
            source=source,
            exit_code=exit_code,
            run_cmd_status=run_cmd_status,
            event_id=event_id,
            diagnostics=rc_fields.get("diagnostics"),
            command=rc_fields.get("command"),
        )
    )

    tool_entry = {
        "toolName": tool_name,
        "toolSuccess": success,
        "toolOutput": output_preview,
        "durationMs": 0,
        "timestamp": ts,
    }
    if active_run:
        append_recent_tool(tool_entry)

    record_task_transcript(
        task_id,
        "tool",
        tool_output[:4000],
        agent=agent_role,
        toolName=tool_name,
        toolSuccess=success,
        toolArgs=tool_args,
        toolOutput=tool_output[:2000],
        source=source,
    )

    label = "Auto QA" if source == "orchestrator" else "Context"
    add_system_log(
        agent_role,
        "success" if success else "error",
        f"{label} {tool_name} — {output_preview[:200]}",
    )


def record_user_tool_evidence(
    task_id: str,
    tool_name: str,
    tool_args: Dict[str, Any],
    tool_output: str,
    *,
    note: str = "",
) -> Dict[str, Any]:
    """Record user-provided tool output on a task transcript and global tool log."""
    from backend.agents.tool_outcomes import (
        classify_run_command,
        normalize_run_command_output,
        parse_run_command_exit,
        run_command_status_label,
    )

    command = str(tool_args.get("command") or "") if tool_name == "run_command" else ""
    normalized = (
        normalize_run_command_output(command, tool_output)
        if tool_name == "run_command"
        else (tool_output or "").strip()
    )
    if tool_name == "run_command":
        outcome = classify_run_command(command, normalized)
        success = outcome != "execution_failed"
    else:
        outcome = "ok"
        success = True

    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    run_id = f"user-inject-{task_id}-{int(time.time() * 1000)}"
    event_id = str(uuid.uuid4())
    output_preview = normalized[:TOOL_OUTPUT_PREVIEW_CHARS]
    rc_fields = _run_command_event_fields(tool_name, tool_args, normalized)
    exit_code = rc_fields.get("exitCode")
    run_cmd_status = None
    if tool_name == "run_command":
        run_cmd_status = run_command_status_label(normalized, success, command)

    content = format_tool_transcript_content(
        tool_name, tool_args, normalized, success=success
    )
    record_task_transcript(
        task_id,
        "tool",
        content,
        agent="User",
        toolName=tool_name,
        toolSuccess=success,
        toolArgs=tool_args,
        toolOutput=normalized[:2000],
        source="user",
    )

    task = find_task_by_id(task_id)
    if task and rc_fields.get("diagnostics"):
        task["lastCommandDiagnostics"] = rc_fields["diagnostics"]

    publish_event(
        "tool_start",
        {
            "eventId": event_id,
            "runId": run_id,
            "taskId": task_id,
            "agent": "User",
            "toolName": tool_name,
            "toolArgs": tool_args,
            "timestamp": ts,
            "source": "user",
        },
    )

    append_global_tool_event(
        _build_history_event(
            run_id=run_id,
            task_id=task_id,
            agent="User",
            tool_name=tool_name,
            tool_args=tool_args,
            tool_output=normalized,
            success=success,
            duration_ms=0,
            timestamp=ts,
            source="user",
            exit_code=exit_code,
            run_cmd_status=run_cmd_status,
            event_id=event_id,
            diagnostics=rc_fields.get("diagnostics"),
            command=rc_fields.get("command"),
        )
    )

    publish_event(
        "tool_end",
        {
            "eventId": event_id,
            "runId": run_id,
            "taskId": task_id,
            "agent": "User",
            "toolName": tool_name,
            "toolArgs": tool_args,
            "toolSuccess": success,
            "toolOutput": output_preview,
            "durationMs": 0,
            "timestamp": ts,
            "source": "user",
            **rc_fields,
            **(
                {"runCommandStatus": run_cmd_status}
                if tool_name == "run_command" and run_cmd_status is not None
                else {}
            ),
        },
    )

    if note.strip():
        record_task_decision(
            task_id,
            "User",
            "inject_evidence",
            note.strip()[:200],
            normalized[:500],
        )
    else:
        record_task_decision(
            task_id,
            "User",
            "inject_evidence",
            f"User injected {tool_name} evidence",
            normalized[:500],
        )

    add_system_log(
        "User",
        "success" if success else "warning",
        f"Injected {tool_name} evidence on {task_id}",
    )

    return {
        "toolOutput": normalized,
        "toolSuccess": success,
        "outcome": outcome,
        "runCommandStatus": run_cmd_status,
    }


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
