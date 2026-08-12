"""Orchestrated fix-verify loop for developer sprint steps."""

from __future__ import annotations

import time
from typing import Any, Dict

from backend.agents.task_context import find_task_by_id, record_task_decision
from backend.services.command_result import format_command_result_for_agent, run_workspace_command
from backend.services.lint_fanout import format_budgeted_problems, maybe_fanout_lint_diagnostics
from backend.services.logs import add_system_log
from backend.services.step_diagnostics import log_event
from backend.services.workflow_settings import get_workflow_settings
from backend.workspace.files import derive_project_lint_command

# Agent results that should not start another expensive fix-verify round.
_HARD_STOP_MARKERS = (
    "stopped:",
    "max tool iterations",
    "timed out:",
    "simulation_fallback",
    "fix-verify aborted",
)


def _is_hard_stop_result(result: str) -> bool:
    lower = (result or "").strip().lower()
    if not lower:
        return False
    if lower == "simulation_fallback":
        return True
    return any(lower.startswith(m) or m in lower[:80] for m in _HARD_STOP_MARKERS)


def run_fix_verify_loop(
    agent,
    task: Dict[str, Any],
    user_prompt: str,
    *,
    max_iterations: int,
) -> str:
    """Run dev agent with lint re-check rounds until clean or cap reached."""
    ws = get_workflow_settings()
    if not (ws.get("enableFixVerifyLoop") or ws.get("requireCleanLint")):
        return agent.execute_step(user_prompt, max_iterations=max_iterations)

    lint_cmd = derive_project_lint_command()
    if not lint_cmd:
        add_system_log(
            "Developer",
            "warning",
            "Fix-verify enabled but no project lint command detected — running single Dev step",
        )
        return agent.execute_step(user_prompt, max_iterations=max_iterations)

    max_rounds = max(1, int(ws.get("maxFixVerifyRounds", 2)))
    max_keep = max(0, int(ws.get("maxInCardLintFixes", 5)))
    abort_on_hard = bool(ws.get("fixVerifyAbortOnHardStop", True))
    task_id = str(task.get("id") or "")
    prompt = user_prompt
    last_result = ""
    iterations_per_round = max(1, int(max_iterations))

    from backend import state as _state

    _state.FIX_VERIFY_MAX_ROUNDS = max_rounds
    try:
        for round_num in range(1, max_rounds + 1):
            if getattr(_state, "SPRINT_CANCEL", False):
                add_system_log(
                    "Developer",
                    "warning",
                    f"Fix-verify aborted at round {round_num}/{max_rounds} (sprint cancelled)",
                )
                log_event("fix_verify_done", "aborted_sprint_cancel")
                return last_result or "Fix-verify aborted: sprint cancelled."

            _state.FIX_VERIFY_ROUND = round_num
            add_system_log(
                "Developer",
                "info",
                f"Fix-verify round {round_num}/{max_rounds} ({iterations_per_round} tool iterations)",
            )
            log_event("fix_verify_start", f"round {round_num}/{max_rounds}")
            last_result = agent.execute_step(prompt, max_iterations=iterations_per_round)

            if abort_on_hard and _is_hard_stop_result(last_result):
                add_system_log(
                    "Developer",
                    "warning",
                    f"Fix-verify aborted after hard stop on round {round_num}: "
                    f"{(last_result or '')[:160]}",
                )
                log_event("fix_verify_done", f"aborted_hard_stop round={round_num}")
                return last_result

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

            # Hybrid: fan out leftovers when over threshold; re-prompt only the in-card budget.
            fanout_task = board_task or task
            fanout = maybe_fanout_lint_diagnostics(
                fanout_task,
                cmd_result.diagnostics,
            )
            kept = fanout.get("kept") or []

            if round_num >= max_rounds:
                record_task_decision(
                    task_id,
                    "Developer",
                    "fix_verify",
                    f"Lint still has {finding_count} issue(s) after {max_rounds} rounds "
                    f"(in-card budget {len(kept)}; spawned {len(fanout.get('spawned') or [])})",
                    detail=cmd_result.summary,
                )
                log_event("fix_verify_done", f"findings remain after {max_rounds} rounds")
                break

            budgeted = format_budgeted_problems(kept or cmd_result.diagnostics, max_keep=max_keep)
            if not budgeted:
                # Fall back to trimmed agent format if budget empty
                budgeted = format_command_result_for_agent(cmd_result)
            spawn_note = ""
            spawned = fanout.get("spawned") or []
            if spawned:
                spawn_note = (
                    f"\nLeftover project lint was split into related Backlog card(s): "
                    f"{', '.join(spawned)}. Do not chase those on this card.\n"
                )
            # Strip prior fix-verify observe blocks so rounds do not accumulate history.
            base = user_prompt
            for marker in (
                "=== OBSERVE (fix-verify round",
                "=== FIX-VERIFY ROUND",
                "=== OBSERVE (fix-verify",
            ):
                if marker in base:
                    base = base.split(marker)[0].rstrip()
            prompt = (
                f"{base}\n\n"
                f"=== OBSERVE (fix-verify round {round_num}/{max_rounds}) ===\n"
                f"Lint still reports issues. Fix only the listed in-card budget "
                f"(at most {max_keep} highest-severity findings relevant to this card's AC). "
                "Do not clear the whole project on this card.\n"
                f"{spawn_note}\n"
                f"{budgeted}"
            )

        return last_result
    finally:
        _state.FIX_VERIFY_ROUND = None
        _state.FIX_VERIFY_MAX_ROUNDS = None
