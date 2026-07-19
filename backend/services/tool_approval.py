"""Block agent tool execution until the user approves or denies."""

import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from backend import state
from backend.agents.tool_outcomes import sanitize_tool_args_for_log
from backend.services.events import publish_event
from backend.services.workflow_settings import get_workflow_settings

APPROVAL_TIMEOUT_SEC = 120


@dataclass
class PendingToolApproval:
    id: str
    run_id: str
    task_id: Optional[str]
    agent: str
    agent_id: str
    tool_name: str
    arguments: Dict[str, Any]
    timestamp: str
    user_prompt: str = ""
    event: threading.Event = field(default_factory=threading.Event, repr=False)
    approved: Optional[bool] = None
    executed: bool = False
    execution_result: Optional[Any] = field(default=None, repr=False)


def _approval_tools() -> List[str]:
    ws = get_workflow_settings()
    tools = list(ws.get("toolApprovalTools") or ["write_file", "run_command"])
    if "write_file" in tools and "apply_patch" not in tools:
        tools.append("apply_patch")
    return tools


def tool_requires_approval(tool_name: str, arguments: Optional[Dict[str, Any]] = None) -> bool:
    ws = get_workflow_settings()
    if not ws.get("requireToolApproval"):
        return False
    if tool_name == "run_command" and arguments:
        from backend.services.command_policy import run_command_requires_approval

        return run_command_requires_approval(str(arguments.get("command") or ""))
    approval_tools = _approval_tools()
    if tool_name in approval_tools:
        return True
    # Custom tools: approve when listed by name, or "customTools" / "*" wildcard
    if "customTools" in approval_tools or "*" in approval_tools:
        from backend.services.custom_tools import get_custom_canonical_names

        if tool_name in get_custom_canonical_names():
            return True
    return False


def non_blocking_approval_enabled() -> bool:
    ws = get_workflow_settings()
    if not ws.get("requireToolApproval"):
        return False
    return ws.get("nonBlockingToolApproval", True) is not False


def list_pending_approvals() -> List[Dict[str, Any]]:
    result: List[Dict[str, Any]] = []
    for item in state.PENDING_TOOL_APPROVALS:
        if isinstance(item, PendingToolApproval) and item.approved is None:
            result.append(
                {
                    "id": item.id,
                    "runId": item.run_id,
                    "taskId": item.task_id,
                    "agent": item.agent,
                    "toolName": item.tool_name,
                    "toolArgs": sanitize_tool_args_for_log(item.tool_name, item.arguments),
                    "timestamp": item.timestamp,
                }
            )
    return result


def queue_tool_approval(
    run_id: str,
    tool_name: str,
    arguments: Dict[str, Any],
    *,
    task_id: Optional[str] = None,
    agent: str = "Developer",
    agent_id: str = "dev",
    user_prompt: str = "",
) -> PendingToolApproval:
    """Queue approval without blocking the caller thread."""
    approval = PendingToolApproval(
        id=uuid.uuid4().hex[:12],
        run_id=run_id,
        task_id=task_id,
        agent=agent,
        agent_id=agent_id,
        tool_name=tool_name,
        arguments=dict(arguments),
        timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        user_prompt=user_prompt,
    )
    state.PENDING_TOOL_APPROVALS.append(approval)
    publish_event(
        "tool_approval_required",
        {
            "id": approval.id,
            "runId": run_id,
            "taskId": task_id or "system",
            "agent": agent,
            "toolName": tool_name,
            "toolArgs": sanitize_tool_args_for_log(tool_name, arguments),
            "timestamp": approval.timestamp,
            "nonBlocking": True,
        },
    )
    return approval


def request_tool_approval(
    run_id: str,
    tool_name: str,
    arguments: Dict[str, Any],
    *,
    task_id: Optional[str] = None,
    agent: str = "Developer",
    agent_id: str = "dev",
    user_prompt: str = "",
) -> tuple[bool, str, Optional[str]]:
    """Block until approved, denied, or timeout. Returns (approved, message, approval_id)."""
    if non_blocking_approval_enabled():
        approval = queue_tool_approval(
            run_id,
            tool_name,
            arguments,
            task_id=task_id,
            agent=agent,
            agent_id=agent_id,
            user_prompt=user_prompt,
        )
        return (
            False,
            f"AWAITING_APPROVAL:{approval.id} — approve in UI to run {tool_name}",
            approval.id,
        )

    approval = PendingToolApproval(
        id=uuid.uuid4().hex[:12],
        run_id=run_id,
        task_id=task_id,
        agent=agent,
        agent_id=agent_id,
        tool_name=tool_name,
        arguments=dict(arguments),
        timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        user_prompt=user_prompt,
    )
    state.PENDING_TOOL_APPROVALS.append(approval)

    publish_event(
        "tool_approval_required",
        {
            "id": approval.id,
            "runId": run_id,
            "taskId": task_id or "system",
            "agent": agent,
            "toolName": tool_name,
            "toolArgs": sanitize_tool_args_for_log(tool_name, arguments),
            "timestamp": approval.timestamp,
        },
    )

    if not approval.event.wait(timeout=APPROVAL_TIMEOUT_SEC):
        approval.approved = False
        _remove_approval(approval)
        return False, "Error: Tool approval timed out", approval.id

    approved = approval.approved is True
    _remove_approval(approval)
    if approved:
        return True, "", approval.id
    return False, "Error: User denied tool execution", approval.id


def resolve_tool_approval(approval_id: str, approved: bool) -> bool:
    for item in state.PENDING_TOOL_APPROVALS:
        if isinstance(item, PendingToolApproval) and item.id == approval_id:
            if item.approved is not None:
                return False
            item.approved = approved
            if approved and not item.executed:
                from backend.services.tool_execution_service import execute_deferred_approval

                execute_deferred_approval(item)
            item.event.set()
            if item.executed or not approved:
                _remove_approval(item)
            return True
    return False


def _remove_approval(approval: PendingToolApproval) -> None:
    try:
        state.PENDING_TOOL_APPROVALS.remove(approval)
    except ValueError:
        pass
