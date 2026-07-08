"""Retry failed agent steps with optional prompt optimization and verification."""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

from backend import state
from backend.agents.registry import AGENT_MAP
from backend.agents.task_context import (
    build_task_prompt,
    find_task_by_id,
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
) -> str:
    from backend.agents.task_context import clear_active_sprint_context, set_active_sprint_context

    set_active_sprint_context(task_id, agent.role)
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
) -> Dict[str, Any]:
    task = find_task_by_id(task_id)
    if not task:
        return {"ok": False, "error": f"Task {task_id} not found"}

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
        optimized = _optimize_prompt(agent, base_prompt, failure_context, task_id, max_iterations=2)
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

    set_active_sprint_context(task_id, agent.role)
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
