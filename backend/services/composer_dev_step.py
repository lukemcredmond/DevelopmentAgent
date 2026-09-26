"""Bounded implementer Dev step (Cursor-like single turn)."""

from __future__ import annotations

import time
from typing import Any, Dict, Optional

from backend.services.logs import add_system_log
from backend.services.step_diagnostics import log_event
from backend.services.workflow_settings import get_workflow_settings


def run_composer_dev_step(
    agent,
    task: Dict[str, Any],
    user_prompt: str,
    *,
    max_iterations: int,
    task_id: Optional[str] = None,
) -> str:
    """
    One bounded Dev turn: optional deterministic fix, single execute_step, optional lint verify.
    """
    ws = get_workflow_settings()
    wall_sec = max(30, int(ws.get("implementerMaxDevStepWallSec") or 180))
    started = time.monotonic()
    tid = task_id or str(task.get("id") or "")

    try:
        from backend.services.sprint_speed_gates import should_force_patch_next_dev_step
        from backend.services.lint_wall_recovery import try_deterministic_missing_pubspec_fix

        if should_force_patch_next_dev_step(task):
            det = try_deterministic_missing_pubspec_fix(task, task_id=tid or None)
            if det:
                add_system_log("Developer", "info", det)
    except Exception:
        pass

    result = agent.execute_step(user_prompt, max_iterations=max(1, int(max_iterations)))
    elapsed = time.monotonic() - started
    if elapsed > wall_sec:
        add_system_log(
            "Developer",
            "warning",
            f"Composer dev step exceeded {wall_sec}s wall ({elapsed:.0f}s)",
        )
        log_event("composer_dev_wall_exceeded", f"{elapsed:.0f}s>{wall_sec}s")

    from backend.services.fix_verify_loop import _agent_had_successful_write, _run_lint_once
    from backend.workspace.files import derive_project_lint_command

    if not _agent_had_successful_write(agent):
        return result

    lint_cmd = derive_project_lint_command()
    if not lint_cmd:
        return result
    try:
        cmd_result, clean = _run_lint_once(tid, task, lint_cmd)
        if clean:
            log_event("composer_dev_lint_clean", lint_cmd[:120])
        else:
            n = len(cmd_result.diagnostics) if cmd_result.diagnostics else 0
            log_event("composer_dev_lint_findings", f"{n} finding(s)")
    except Exception:
        pass
    return result
