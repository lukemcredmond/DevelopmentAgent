"""System lint verify after Dev writes — satisfies lane advance without model-initiated analyze."""

from __future__ import annotations

import time
from typing import Any, Dict, Optional

from backend import state
from backend.services.logs import add_system_log
from backend.services.step_diagnostics import _write_tools_succeeded, get_active_trace, log_event, log_tool
from backend.services.workflow_settings import get_workflow_settings
from backend.workspace.files import derive_project_lint_command


def system_auto_verify_enabled(ws: Optional[Dict[str, Any]] = None) -> bool:
    if ws is None:
        ws = get_workflow_settings()
    return bool(ws.get("enableSystemAutoVerify", True))


def maybe_run_system_auto_verify(
    task_id: str,
    task: Dict[str, Any],
    *,
    allow_without_writes: bool = False,
) -> bool:
    """Run project lint after writes when enabled. Returns True if verify ran."""
    if not system_auto_verify_enabled():
        return False
    trace = get_active_trace()
    if not trace:
        return False
    tools_log = getattr(trace, "tools_log", None) or []
    wrote = _write_tools_succeeded(tools_log)
    if not wrote and not allow_without_writes:
        return False
    if not wrote and allow_without_writes:
        advanced = any(
            isinstance(t, dict)
            and t.get("toolName") == "update_board"
            and t.get("success")
            for t in tools_log
        )
        if not advanced:
            return False
    from backend.services.duplicate_tool_policy import tools_log_has_verify

    if tools_log_has_verify(tools_log):
        return False
    lint_cmd = derive_project_lint_command()
    if not lint_cmd:
        return False
    from backend.services.fix_verify_loop import _run_lint_once

    cmd_result, clean = _run_lint_once(task_id, task, lint_cmd)
    finding_count = len(cmd_result.diagnostics) if cmd_result.diagnostics else 0
    summary = f"system_verify {lint_cmd} ({finding_count} findings, {cmd_result.outcome})"
    duration_ms = int(getattr(cmd_result, "duration_ms", 0) or 0)
    log_tool("system_verify", clean, summary, duration_ms=duration_ms)
    log_event("system_auto_verify", f"{lint_cmd} clean={clean}")
    add_system_log(
        "System",
        "info" if clean else "warning",
        f"Auto-verify finished — {finding_count} finding(s), clean={clean}",
    )
    return True
