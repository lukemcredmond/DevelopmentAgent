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
    tool_name: str
    arguments: Dict[str, Any]
    timestamp: str
    event: threading.Event = field(default_factory=threading.Event, repr=False)
    approved: Optional[bool] = None


def _approval_tools() -> List[str]:
    ws = get_workflow_settings()
    tools = list(ws.get("toolApprovalTools") or ["write_file", "run_command"])
    if "write_file" in tools and "apply_patch" not in tools:
        tools.append("apply_patch")
    return tools


def tool_requires_approval(tool_name: str) -> bool:
    ws = get_workflow_settings()
    if not ws.get("requireToolApproval"):
        return False
    return tool_name in _approval_tools()


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


def request_tool_approval(
    run_id: str,
    tool_name: str,
    arguments: Dict[str, Any],
    *,
    task_id: Optional[str] = None,
    agent: str = "Developer",
) -> tuple[bool, str]:
    """Block until approved, denied, or timeout. Returns (approved, message)."""
    approval = PendingToolApproval(
        id=uuid.uuid4().hex[:12],
        run_id=run_id,
        task_id=task_id,
        agent=agent,
        tool_name=tool_name,
        arguments=dict(arguments),
        timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
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
        return False, "Error: Tool approval timed out"

    approved = approval.approved is True
    _remove_approval(approval)
    if approved:
        return True, ""
    return False, "Error: User denied tool execution"


def resolve_tool_approval(approval_id: str, approved: bool) -> bool:
    for item in state.PENDING_TOOL_APPROVALS:
        if isinstance(item, PendingToolApproval) and item.id == approval_id:
            if item.approved is not None:
                return False
            item.approved = approved
            item.event.set()
            return True
    return False


def _remove_approval(approval: PendingToolApproval) -> None:
    try:
        state.PENDING_TOOL_APPROVALS.remove(approval)
    except ValueError:
        pass
