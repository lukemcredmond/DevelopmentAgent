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
    wall_sec = max(30, int(ws.get("implementerMaxDevStepWallSec") or 600))
    mult = 1.0
    if (task.get("integrationEditPaths") or []) or str(task.get("workType") or "").lower() == "integration":
        try:
            mult = float(ws.get("integrationStepBudgetMultiplier") or 2.0)
        except (TypeError, ValueError):
            mult = 2.0
        mult = max(1.0, min(mult, 4.0))
        wall_sec = max(30, int(wall_sec * mult))
    started = time.monotonic()
    tid = task_id or str(task.get("id") or "")
    max_iterations = max(1, int(max_iterations * mult))

    try:
        from backend import state as _state

        _state.DEV_STEP_INTRASTEP_WALL = False
    except Exception:
        pass

    try:
        from backend.services.sprint_speed_gates import should_force_patch_next_dev_step
        from backend.services.lint_wall_recovery import (
            try_deterministic_main_dart_scaffold_fix,
            try_deterministic_missing_pubspec_fix,
        )

        if should_force_patch_next_dev_step(task):
            for det_fn in (
                try_deterministic_missing_pubspec_fix,
                try_deterministic_main_dart_scaffold_fix,
            ):
                det = det_fn(task, task_id=tid or None)
                if det:
                    add_system_log("Developer", "info", det)
                    break
    except Exception:
        pass

    result = agent.execute_step(
        user_prompt,
        max_iterations=max(1, int(max_iterations)),
        max_step_duration_sec=wall_sec,
    )
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

    if getattr(agent, "_fix_verify_ran_this_step", False):
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
            live_board = None
            try:
                from backend.agents.task_context import find_task_by_id

                live_board = find_task_by_id(tid) if tid else None
            except Exception:
                live_board = None
            if live_board and str(live_board.get("lane") or "") == "Done":
                try:
                    from backend.services.board_service import move_board_stage

                    move_board_stage(tid, "In Progress", honor_dev_claim_gate=False)
                    add_system_log(
                        "Developer",
                        "warning",
                        "Reopened card to In Progress — lint failed after premature Done",
                    )
                except Exception:
                    pass
            if ws.get("enableFixVerifyLoop") and not clean:
                from backend.services.fix_verify_loop import run_fix_verify_loop

                rounds = max(1, int(ws.get("maxFixVerifyRounds") or 2))
                fix_prompt = (
                    f"Fix analyzer findings from `{lint_cmd}` on this card. "
                    "Use read_file and apply_patch; do not stop until lint is clean."
                )
                result = run_fix_verify_loop(
                    agent,
                    task,
                    fix_prompt,
                    max_iterations=max(1, int(max_iterations)),
                )
    except Exception:
        pass
    return result
