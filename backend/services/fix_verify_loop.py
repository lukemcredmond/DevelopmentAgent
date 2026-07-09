"""Orchestrated fix-verify loop for developer sprint steps."""

from __future__ import annotations

import time
from typing import Any, Dict

from backend.agents.task_context import find_task_by_id, record_task_decision
from backend.services.command_result import format_command_result_for_agent, run_workspace_command
from backend.services.logs import add_system_log
from backend.services.step_diagnostics import log_event
from backend.services.workflow_settings import get_workflow_settings
from backend.workspace.files import derive_project_lint_command


def run_fix_verify_loop(
    agent,
    task: Dict[str, Any],
    user_prompt: str,
    *,
    max_iterations: int,
) -> str:
    """Run dev agent with lint re-check rounds until clean or cap reached."""
    ws = get_workflow_settings()
    if not ws.get("enableFixVerifyLoop"):
        return agent.execute_step(user_prompt, max_iterations=max_iterations)

    lint_cmd = derive_project_lint_command()
    if not lint_cmd:
        return agent.execute_step(user_prompt, max_iterations=max_iterations)

    max_rounds = max(1, int(ws.get("maxFixVerifyRounds", 3)))
    iterations_per_round = max(4, max_iterations // 2)
    task_id = str(task.get("id") or "")
    prompt = user_prompt
    last_result = ""

    for round_num in range(1, max_rounds + 1):
        add_system_log(
            "Developer",
            "info",
            f"Fix-verify round {round_num}/{max_rounds} ({iterations_per_round} tool iterations)",
        )
        log_event("fix_verify_start", f"round {round_num}/{max_rounds}")
        last_result = agent.execute_step(prompt, max_iterations=iterations_per_round)

        lint_started = time.time()
        cmd_result = run_workspace_command(lint_cmd)
        lint_duration_ms = int((time.time() - lint_started) * 1000)
        finding_count = len(cmd_result.diagnostics) if cmd_result.diagnostics else 0
        add_system_log(
            "Developer",
            "info",
            f"Fix-verify lint finished in {lint_duration_ms}ms — {finding_count} finding(s)",
        )
        log_event(
            "lint_run",
            f"{lint_cmd} {lint_duration_ms}ms findings={finding_count} outcome={cmd_result.outcome}",
        )
        board_task = find_task_by_id(task_id)
        if board_task:
            if cmd_result.diagnostics:
                board_task["lastCommandDiagnostics"] = cmd_result.diagnostics[:50]
            else:
                board_task["lastCommandDiagnostics"] = []

        if cmd_result.outcome == "ok" or not cmd_result.diagnostics:
            record_task_decision(
                task_id,
                "Developer",
                "fix_verify",
                f"Lint clean after round {round_num}",
                detail=cmd_result.summary or "no findings",
            )
            log_event("fix_verify_done", f"clean after round {round_num}")
            return last_result

        if round_num >= max_rounds:
            record_task_decision(
                task_id,
                "Developer",
                "fix_verify",
                f"Lint still has {len(cmd_result.diagnostics)} issue(s) after {max_rounds} rounds",
                detail=cmd_result.summary,
            )
            log_event("fix_verify_done", f"findings remain after {max_rounds} rounds")
            break

        problems = format_command_result_for_agent(cmd_result)
        prompt = (
            f"{user_prompt}\n\n"
            f"=== FIX-VERIFY ROUND {round_num}/{max_rounds} ===\n"
            "Lint still reports issues. Fix every file:line below before continuing.\n\n"
            f"{problems}"
        )

    return last_result
