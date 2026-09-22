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


def maybe_run_system_auto_verify(task_id: str, task: Dict[str, Any]) -> bool:
    """Run project lint after writes when enabled. Returns True if verify ran."""
    if not system_auto_verify_enabled():
        return False
    trace = get_active_trace()
    if not trace:
        return False
    tools_log = getattr(trace, "tools_log", None) or []
    if not _write_tools_succeeded(tools_log):
        return False
    from backend.services.duplicate_tool_policy import tools_log_has_verify

    if tools_log_has_verify(tools_log):
        return False
    lint_cmd = derive_project_lint_command()
    if not lint_cmd:
        return False
    from backend.services.command_result import run_workspace_command

    started = time.time()
    add_system_log("System", "info", f"Auto-verify: {lint_cmd}")
    result = run_workspace_command(lint_cmd)
    duration_ms = int((time.time() - started) * 1000)
    finding_count = len(result.diagnostics) if result.diagnostics else 0
    clean = result.outcome == "ok" or not result.diagnostics
    state.FIX_VERIFY_LINT_CLEAN = clean
    live = task
    if live is not None:
        live["fixVerifyLintClean"] = clean
    summary = f"system_verify {lint_cmd} ({finding_count} findings, {result.outcome})"
    log_tool("system_verify", clean, summary, duration_ms=duration_ms)
    log_event("system_auto_verify", f"{lint_cmd} {duration_ms}ms clean={clean}")
    add_system_log(
        "System",
        "info" if clean else "warning",
        f"Auto-verify finished in {duration_ms}ms — {finding_count} finding(s), clean={clean}",
    )
    return True
