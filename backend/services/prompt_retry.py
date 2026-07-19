"""Retry failed agent steps with optional prompt optimization and verification."""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

from backend import state
from backend.agents.registry import AGENT_MAP
from backend.agents.task_context import (
    build_task_prompt,
    find_task_by_id,
    is_task_done,
    normalize_task,
    record_task_decision,
    record_task_transcript,
    set_active_sprint_context,
)
from backend.services.llm_debug_log import get_llm_logs
from backend.services.project_service import save_current_project_state
from backend.services.sprint_service import set_project_brief
from backend.services.workflow_settings import get_workflow_settings


def _memory_context_for_retry(agent, task_id: str, query: str) -> str:
    project_id = state.CURRENT_PROJECT_ID or "default-proj"
    hits = agent.memory.search(
        agent.role,
        query,
        limit=3,
        project_id=project_id,
        include_all_agents=True,
    )
    if not hits:
        return ""
    return "\n".join(f"[{item['category']}] {item['content']}" for item in hits)


def _last_failure_context(task_id: str, agent=None) -> str:
    logs = get_llm_logs(limit=5, task_id=task_id)
    parts = []
    for entry in logs:
        if entry.get("error"):
            parts.append(f"Error: {entry['error']}")
        if entry.get("responseContent"):
            parts.append(f"Response: {entry['responseContent'][:500]}")
    task = find_task_by_id(task_id)
    if task:
        ld = task.get("lastDiagnosis")
        if isinstance(ld, dict) and ld.get("problem"):
            parts.append(
                f"Diagnosis: {ld.get('problem')} → {ld.get('recommendedAction', '')}"
            )
        diagnostics = task.get("lastCommandDiagnostics") or []
        for item in diagnostics[:10]:
            parts.append(
                f"Lint {item.get('severity')} {item.get('file')}:{item.get('line')} "
                f"{item.get('message', '')}"
            )
        recent = list(reversed(task.get("transcript") or []))[:5]
        for entry in recent:
            if entry.get("toolSuccess") is False:
                parts.append(
                    f"Tool fail {entry.get('toolName')}: {str(entry.get('content', ''))[:200]}"
                )
        if agent is not None:
            memory_block = _memory_context_for_retry(
                agent,
                task_id,
                "\n".join(parts) or task.get("title", ""),
            )
            if memory_block:
                parts.append(f"Memory:\n{memory_block}")
    return "\n".join(parts) or "Unknown failure"


def _auto_diagnose_if_needed(task_id: str, ollama_url: str) -> None:
    task = find_task_by_id(task_id)
    if not task:
        return
    if isinstance(task.get("lastDiagnosis"), dict) and task["lastDiagnosis"].get("problem"):
        return
    from backend.services.task_diagnosis import diagnose_task

    diagnose_task(task_id, ollama_url)


def _post_retry_verification(task_id: str) -> Dict[str, Any]:
    from backend.services.command_result import run_workspace_command
    from backend.workspace.files import derive_project_lint_command

    lint_cmd = derive_project_lint_command()
    if not lint_cmd:
        return {"status": "skipped", "diagnosticsCount": 0, "command": None}

    result = run_workspace_command(lint_cmd)
    task = find_task_by_id(task_id)
    if task:
        task["lastCommandDiagnostics"] = (
            result.diagnostics[:50] if result.diagnostics else []
        )

    status = "clean" if not result.diagnostics else "findings"
    return {
        "status": status,
        "diagnosticsCount": len(result.diagnostics),
        "summary": result.summary,
        "outcome": result.outcome,
        "command": lint_cmd,
    }


def _optimize_prompt(
    agent,
    original_prompt: str,
    failure_context: str,
    task_id: str,
    max_iterations: int = 1,
    *,
    allow_done_retry: bool = False,
) -> str:
    from backend.agents.task_context import clear_active_sprint_context, set_active_sprint_context

    set_active_sprint_context(task_id, agent.role, allow_done_retry=allow_done_retry)
    try:
        optimizer_prompt = (
            "You are a prompt engineer. The following agent prompt failed or produced poor results.\n"
            "Rewrite the USER TASK PROMPT to be clearer, shorter, and more actionable. "
            "Fix ambiguity and reference concrete files/steps when possible.\n"
            "Reply with ONLY the improved prompt text — no explanation.\n\n"
            f"=== FAILURE CONTEXT ===\n{failure_context[:2000]}\n\n"
            f"=== ORIGINAL PROMPT ===\n{original_prompt[:4000]}"
        )
        optimized = agent.execute_step(optimizer_prompt, max_iterations=max_iterations)
        if optimized and optimized != "SIMULATION_FALLBACK" and len(optimized.strip()) > 20:
            return optimized.strip()
        return original_prompt
    finally:
        clear_active_sprint_context()


def retry_agent_step(
    task_id: str,
    agent_id: str,
    ollama_url: str,
    *,
    mode: str = "same",
    brief: str = "",
    reason: str = "user_requested",
    allow_done_retry: bool = False,
) -> Dict[str, Any]:
    task = find_task_by_id(task_id)
    if not task:
        return {"ok": False, "error": f"Task {task_id} not found"}

    if is_task_done(task_id) and not allow_done_retry:
        return {
            "ok": False,
            "error": (
                f"Task {task_id} is already Done. "
                "Pass allowDoneRetry=true for a deliberate re-run."
            ),
        }

    agent = AGENT_MAP.get(agent_id)
    if not agent:
        return {"ok": False, "error": f"Unknown agent: {agent_id}"}

    normalize_task(task)
    if brief:
        set_project_brief(brief, source="user")
    elif state.PROJECT_BRIEF:
        brief = state.PROJECT_BRIEF

    agent.ollama_url = ollama_url
    ws = get_workflow_settings()
    max_iter = int(ws.get("maxLlmIterationsPerStep", 8))

    if mode in ("optimized", "fix_and_verify"):
        _auto_diagnose_if_needed(task_id, ollama_url)

    base_prompt = build_task_prompt(task, brief)
    failure_context = _last_failure_context(task_id, agent=agent)

    if mode == "optimized":
        optimized = _optimize_prompt(
            agent,
            base_prompt,
            failure_context,
            task_id,
            max_iterations=2,
            allow_done_retry=allow_done_retry,
        )
        record_task_decision(
            task_id,
            agent.role,
            "prompt_retry",
            f"Optimized retry ({reason})",
            detail=json.dumps({"originalLen": len(base_prompt), "optimizedLen": len(optimized)}),
        )
        user_prompt = optimized
    elif mode == "fix_and_verify":
        record_task_decision(
            task_id,
            agent.role,
            "prompt_retry",
            f"Fix-and-verify retry ({reason})",
            detail=failure_context[:500],
        )
        user_prompt = (
            f"{base_prompt}\n\n=== RETRY CONTEXT ===\n{failure_context[:2500]}\n"
            "Fix all listed lint issues, then verify with the project lint command."
        )
    else:
        user_prompt = base_prompt
        record_task_decision(task_id, agent.role, "prompt_retry", f"Same prompt retry ({reason})")

    try:
        set_active_sprint_context(task_id, agent.role, allow_done_retry=allow_done_retry)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    try:
        if mode == "fix_and_verify":
            from backend.services.fix_verify_loop import run_fix_verify_loop

            output = run_fix_verify_loop(
                agent,
                task,
                user_prompt,
                max_iterations=max_iter,
            )
        else:
            output = agent.execute_step(user_prompt, max_iterations=max_iter)
    finally:
        from backend.agents.task_context import clear_active_sprint_context

        clear_active_sprint_context()

    verification = _post_retry_verification(task_id)
    success = (
        output not in ("SIMULATION_FALLBACK",)
        and not str(output).startswith("Stopped:")
    )
    if output:
        record_task_transcript(task_id, "assistant", output[:2000], agent=agent.role)

    save_current_project_state()
    return {
        "ok": success,
        "mode": mode,
        "output": output,
        "optimizedPrompt": user_prompt if mode in ("optimized", "fix_and_verify") else None,
        "verification": verification,
    }


def build_continuation_prompt(task: Dict[str, Any], brief: str, progress: Dict[str, Any]) -> str:
    """Prompt for Extend: continue from prior max-iter progress (no in-memory LLM history)."""
    base = build_task_prompt(task, brief)
    tools = progress.get("toolsUsed") or []
    tools_line = ", ".join(tools) if tools else "(none recorded)"
    last_tools = progress.get("lastTools") or []
    last_lines = []
    for entry in last_tools:
        ok = "ok" if entry.get("success") is not False else "FAIL"
        last_lines.append(
            f"- {entry.get('toolName')} [{ok}]: {str(entry.get('summary') or '')[:100]}"
        )
    files = []
    for f in task.get("files") or []:
        if isinstance(f, dict) and f.get("path"):
            files.append(f"{f.get('path')} ({f.get('action') or 'touched'})")
    files_line = ", ".join(files[:12]) if files else "(none)"
    iters = f"{progress.get('iterationsUsed', '?')}/{progress.get('iterationsMax', '?')}"
    stuck = progress.get("stuckLoop")
    block = (
        f"=== CONTINUATION (previous step hit max LLM iterations {iters}) ===\n"
        f"Tools already used: {tools_line}\n"
        f"Files touched: {files_line}\n"
        f"Plan rejections: {progress.get('planRejections', 0)}; "
        f"text rejections: {progress.get('textRejections', 0)}\n"
    )
    if last_lines:
        block += "Recent tool results:\n" + "\n".join(last_lines) + "\n"
    if progress.get("lastToolSummary"):
        block += f"Last tool: {progress['lastToolSummary']}\n"
    if stuck:
        block += (
            "WARNING: Prior step showed repeated failing tool args — change approach; "
            "do not repeat the same failed call.\n"
        )
    block += (
        "Continue from here. Do not redo completed work. "
        "Call apply_patch or write_file to finish remaining edits. "
        "This is a new step with context from what already ran (message history was not saved)."
    )
    return f"{base}\n\n{block}"


def extend_agent_step(
    task_id: str,
    agent_id: str,
    ollama_url: str,
    *,
    action: str = "extend",
    extra_iterations: int = 4,
    brief: str = "",
    allow_done_retry: bool = False,
) -> Dict[str, Any]:
    """Extend (+N iterations with continuation) or reset (fresh default step)."""
    if action == "reset":
        result = retry_agent_step(
            task_id,
            agent_id,
            ollama_url,
            mode="same",
            brief=brief,
            reason="max_iter_reset",
            allow_done_retry=allow_done_retry,
        )
        if result.get("ok") is not False or not result.get("error"):
            task = find_task_by_id(task_id)
            if task:
                record_task_decision(
                    task_id,
                    AGENT_MAP[agent_id].role if agent_id in AGENT_MAP else "System",
                    "max_iter_reset",
                    "Reset iteration budget — fresh step",
                )
        return {**result, "action": "reset"}

    task = find_task_by_id(task_id)
    if not task:
        return {"ok": False, "error": f"Task {task_id} not found"}

    if is_task_done(task_id) and not allow_done_retry:
        return {
            "ok": False,
            "error": (
                f"Task {task_id} is already Done. "
                "Pass allowDoneRetry=true for a deliberate re-run."
            ),
        }

    agent = AGENT_MAP.get(agent_id)
    if not agent:
        return {"ok": False, "error": f"Unknown agent: {agent_id}"}

    normalize_task(task)
    if brief:
        set_project_brief(brief, source="user")
    elif state.PROJECT_BRIEF:
        brief = state.PROJECT_BRIEF

    extra = max(1, min(int(extra_iterations or 4), 16))
    progress = (
        state.LAST_STEP_PROGRESS
        or (task.get("lastStepProgress") if isinstance(task.get("lastStepProgress"), dict) else None)
        or {}
    )
    if state.LAST_STEP_OUTCOME and isinstance(state.LAST_STEP_OUTCOME.get("stepProgress"), dict):
        progress = state.LAST_STEP_OUTCOME["stepProgress"] or progress

    agent.ollama_url = ollama_url
    user_prompt = build_continuation_prompt(task, brief, progress)
    record_task_decision(
        task_id,
        agent.role,
        "max_iter_extend",
        f"Extended step by +{extra} LLM iterations",
        detail=json.dumps({"extraIterations": extra, "toolsUsed": progress.get("toolsUsed")}),
    )

    state.STEP_FILE_READS.clear()
    state.STEP_PATCH_FAILURES.clear()

    try:
        set_active_sprint_context(task_id, agent.role, allow_done_retry=allow_done_retry)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}

    from backend.services.sprint_service import (
        _ensure_dev_step_trace,
        _record_last_step_outcome,
        get_task_lane,
    )
    from backend.services.step_diagnostics import finalize_active_step_trace

    lane_before = get_task_lane(task_id) or "In Progress"
    if agent_id == "dev":
        try:
            _ensure_dev_step_trace(task_id, task.get("title") or task_id, lane_before)
        except Exception:
            pass

    try:
        output = agent.execute_step(user_prompt, max_iterations=extra)
        state.LAST_AGENT_STEP_RESULT = output
        _record_last_step_outcome(
            task_id, lane_before, agent.role, agent_result=output
        )
        try:
            finalize_active_step_trace(
                lane_after=get_task_lane(task_id) or lane_before,
                agent_result=output,
            )
        except Exception:
            pass
    finally:
        from backend.agents.task_context import clear_active_sprint_context

        clear_active_sprint_context()

    success = (
        output not in ("SIMULATION_FALLBACK",)
        and not str(output).startswith("Stopped:")
        and not str(output).startswith("Max tool iterations")
    )
    if output:
        record_task_transcript(task_id, "assistant", output[:2000], agent=agent.role)

    save_current_project_state()
    return {
        "ok": success,
        "action": "extend",
        "extraIterations": extra,
        "output": output,
        "continuationPrompt": user_prompt[:500],
    }
