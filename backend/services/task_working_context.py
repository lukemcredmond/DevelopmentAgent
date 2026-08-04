"""Compact per-card facts shared across agents (prompt inject, not a separate DB)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

WORKING_CONTEXT_MAX = 24


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def normalize_working_context_fields(task: Dict[str, Any]) -> None:
    raw = task.get("workingContext")
    if not isinstance(raw, list):
        task["workingContext"] = []
    else:
        cleaned: List[Dict[str, str]] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            summary = str(item.get("summary") or "").strip()
            if not summary:
                continue
            cleaned.append(
                {
                    "at": str(item.get("at") or ""),
                    "kind": str(item.get("kind") or "fact"),
                    "summary": summary[:500],
                }
            )
        task["workingContext"] = cleaned[-WORKING_CONTEXT_MAX:]
    gen = task.get("workspaceGeneration")
    task["workspaceGeneration"] = int(gen) if isinstance(gen, (int, float)) and gen >= 0 else 0
    if task.get("contextUpdatedAt") is not None:
        task["contextUpdatedAt"] = str(task["contextUpdatedAt"])


def bump_workspace_generation(task: Dict[str, Any]) -> None:
    normalize_working_context_fields(task)
    task["workspaceGeneration"] = int(task.get("workspaceGeneration") or 0) + 1
    task["contextUpdatedAt"] = _now_iso()


def append_working_context(
    task: Dict[str, Any],
    *,
    kind: str,
    summary: str,
) -> None:
    text = (summary or "").strip()
    if not text:
        return
    normalize_working_context_fields(task)
    entries = list(task.get("workingContext") or [])
    entries.append({"at": _now_iso(), "kind": kind, "summary": text[:500]})
    task["workingContext"] = entries[-WORKING_CONTEXT_MAX:]
    task["contextUpdatedAt"] = _now_iso()


def format_working_context_for_prompt(task: Dict[str, Any], *, max_lines: int = 12) -> str:
    normalize_working_context_fields(task)
    entries = task.get("workingContext") or []
    if not entries:
        return ""
    gen = task.get("workspaceGeneration") or 0
    updated = task.get("contextUpdatedAt") or ""
    lines = [
        "=== WORKING CONTEXT (this card — shared across agents) ===",
        f"workspaceGeneration={gen}" + (f" updated={updated}" if updated else ""),
    ]
    for item in entries[-max_lines:]:
        if not isinstance(item, dict):
            continue
        at = item.get("at") or "?"
        kind = item.get("kind") or "fact"
        lines.append(f"- [{kind} @ {at}] {item.get('summary', '')}")
    from backend.services.prompt_profile import is_local_slm_profile

    if not is_local_slm_profile():
        lines.append(
            "Treat this as current truth for this card; re-run commands after workspaceGeneration increases."
        )
    return "\n".join(lines) + "\n"


def record_tool_working_context(
    task: Optional[Dict[str, Any]],
    *,
    tool_name: str,
    arguments: Dict[str, Any],
    tool_output: str,
    success: bool,
) -> None:
    if not task:
        return
    from backend.agents.tool_outcomes import parse_run_command_exit, summarize_tool_args

    if tool_name in ("write_file", "apply_patch"):
        bump_workspace_generation(task)
        path = str(arguments.get("path") or "?")
        append_working_context(
            task,
            kind="write",
            summary=f"{tool_name} {'ok' if success else 'FAIL'} → {path}",
        )
        return

    if tool_name == "run_command":
        cmd = str(arguments.get("command") or "")[:200]
        exit_code, body = parse_run_command_exit(tool_output or "")
        status = "ok" if success else "FAIL"
        if exit_code is not None:
            status = f"exit {exit_code}"
        snippet = (body or tool_output or "").replace("\n", " ")[:120]
        append_working_context(
            task,
            kind="command",
            summary=f"run_command {status}: {cmd} — {snippet}",
        )
        return

    if tool_name == "read_file" and success:
        path = str(arguments.get("path") or "?")
        append_working_context(
            task,
            kind="read",
            summary=f"read_file ok: {path}",
        )
        return

    if tool_name == "list_dir" and success:
        path = str(arguments.get("path") or ".")
        append_working_context(
            task,
            kind="explore",
            summary=f"list_dir ok: {path}",
        )
        return

    if tool_name == "grep" and success:
        pattern = str(arguments.get("pattern") or "")[:80]
        path = str(arguments.get("path") or "")
        where = f" in {path}" if path else ""
        append_working_context(
            task,
            kind="explore",
            summary=f"grep ok: '{pattern}'{where}",
        )


def save_task_fact_memory(
    *,
    task_id: str,
    agent_role: str,
    content: str,
    category: str = "fact",
) -> None:
    """Phase B: persist verification/command facts for semantic retrieval."""
    text = (content or "").strip()
    if not text or not task_id:
        return
    ws = __import__("backend.services.workflow_settings", fromlist=["get_workflow_settings"]).get_workflow_settings()
    if not ws.get("enableStepLessonMemory", True):
        return
    try:
        from backend import state
        from backend.storage.memory_engine import create_memory_engine

        note = f"[task:{task_id}] {text}"[:2000]
        engine = create_memory_engine()
        engine.save_project_note(
            agent_role,
            note,
            category=category,
            project_id=state.CURRENT_PROJECT_ID,
        )
    except Exception:
        pass
