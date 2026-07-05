"""Retry failed agent steps with optional prompt optimization."""

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


def _last_failure_context(task_id: str) -> str:
    logs = get_llm_logs(limit=5, task_id=task_id)
    parts = []
    for entry in logs:
        if entry.get("error"):
            parts.append(f"Error: {entry['error']}")
        if entry.get("responseContent"):
            parts.append(f"Response: {entry['responseContent'][:500]}")
    task = find_task_by_id(task_id)
    if task:
        recent = list(reversed(task.get("transcript") or []))[:5]
        for entry in recent:
            if entry.get("toolSuccess") is False:
                parts.append(f"Tool fail {entry.get('toolName')}: {str(entry.get('content', ''))[:200]}")
    return "\n".join(parts) or "Unknown failure"


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

    base_prompt = build_task_prompt(task, brief)
    failure_context = _last_failure_context(task_id)

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
    else:
        user_prompt = base_prompt
        record_task_decision(task_id, agent.role, "prompt_retry", f"Same prompt retry ({reason})")

    set_active_sprint_context(task_id, agent.role)
    try:
        output = agent.execute_step(user_prompt, max_iterations=max_iter)
    finally:
        from backend.agents.task_context import clear_active_sprint_context

        clear_active_sprint_context()

    success = output not in ("SIMULATION_FALLBACK",) and not str(output).startswith("Stopped:")
    if output:
        record_task_transcript(task_id, "assistant", output[:2000], agent=agent.role)

    save_current_project_state()
    return {
        "ok": success,
        "mode": mode,
        "output": output,
        "optimizedPrompt": user_prompt if mode == "optimized" else None,
    }
