"""Structured step recap for local/weaker models — goal, tool intent, dedupe, next action."""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from backend.agents.task_context import find_task_by_id, normalize_task
from backend.agents.tool_outcomes import summarize_tool_args
from backend.services.task_working_context import format_working_context_for_prompt

STEP_RECAP_MARKER = "=== STEP RECAP (local model aid) ==="
STEP_GOAL_MARKER = "=== STEP GOAL (this sprint step) ==="

ToolBatchItem = Tuple[str, Dict[str, Any], Any]


def step_recap_enabled(ws: Optional[Dict[str, Any]] = None) -> bool:
    from backend.services.workflow_settings import get_workflow_settings

    settings = ws if ws is not None else get_workflow_settings()
    return settings.get("enableAgentStepRecap") is not False


def _task_goal_lines(task: Dict[str, Any], *, max_ac: int = 3) -> List[str]:
    normalize_task(task)
    lines = [f"Card: {task.get('id')} — {task.get('title', '')}"]
    lane = str(task.get("status") or "")
    if lane:
        lines.append(f"Lane: {lane}")
    acs = [str(c).strip() for c in (task.get("acceptanceCriteria") or []) if str(c).strip()]
    if acs:
        lines.append("Acceptance criteria:")
        for i, ac in enumerate(acs[:max_ac], start=1):
            lines.append(f"  {i}. {ac[:220]}")
        if len(acs) > max_ac:
            lines.append(f"  … +{len(acs) - max_ac} more in Task Detail above")
    return lines


def build_step_goal_anchor(agent_role: str, task: Optional[Dict[str, Any]]) -> str:
    if not task:
        return ""
    lines = [STEP_GOAL_MARKER, f"Agent: {agent_role}"]
    lines.extend(_task_goal_lines(task))
    lines.append(
        "Use Task Detail above for full context. Do not ask for the brief again. "
        "Prefer tools over long text; do not repeat identical tool calls."
    )
    wc = format_working_context_for_prompt(task, max_lines=6)
    if wc.strip():
        lines.append("")
        lines.append(wc.strip())
    return "\n".join(lines)


def _tool_intent_line(tool_name: str, arguments: Dict[str, Any]) -> str:
    args = arguments or {}
    path = str(args.get("path") or args.get("test_script_path") or "").strip()
    pattern = str(args.get("pattern") or "").strip()
    cmd = str(args.get("command") or "").strip()
    if tool_name == "list_dir":
        return f"Intent: inventory workspace at '{path or '.'}' (find manifests, lib/, entrypoints)."
    if tool_name == "read_file":
        return f"Intent: read '{path}' to satisfy AC (copy text from tool output for patches)."
    if tool_name == "grep":
        extra = f" in '{path}'" if path else ""
        return f"Intent: search for '{pattern[:60]}'{extra} — use matches; do not re-grep same pattern."
    if tool_name == "glob_file_search":
        return f"Intent: find files matching '{str(args.get('pattern') or args.get('glob') or '?')[:60]}'."
    if tool_name == "run_command":
        return f"Intent: run shell check — `{cmd[:100]}` (do not repeat after success)."
    if tool_name == "apply_patch":
        return f"Intent: edit existing file '{path}' using verbatim old_text from read_file."
    if tool_name == "write_file":
        return f"Intent: create/overwrite '{path}'."
    if tool_name in ("update_board", "add_backlog_tasks", "add_subtasks"):
        return f"Intent: board/backlog update via {tool_name}."
    return f"Intent: {tool_name}({summarize_tool_args(tool_name, args)[:80]})"


def _suggest_next_action(
    agent_role: str,
    *,
    tools_used_step: Set[str],
    last_tool: str,
    last_ok: bool,
) -> str:
    role = agent_role or ""
    readonly = tools_used_step & {
        "list_dir",
        "grep",
        "glob_file_search",
        "read_file",
        "search_code",
        "semantic_search",
    }
    writes = tools_used_step & {"write_file", "apply_patch"}
    board = tools_used_step & {"update_board", "add_backlog_tasks", "add_subtasks"}

    if role == "Product Owner":
        if readonly and not board:
            return (
                "Next: use tool output above — update_board, add_backlog_tasks, add_subtasks, "
                "or JSON description/acceptanceCriteria per Task Detail. No onboarding text."
            )
        if not tools_used_step:
            return "Next: grep/glob/list_dir to explore, then board tools or JSON — not a greeting."
        return "Next: complete PO action (board move or JSON); do not repeat exploration tools."

    if role == "Developer":
        if last_tool == "read_file" and last_ok and not writes:
            return "Next: apply_patch on that path (old_text from read_file output) or run_command verify."
        if last_tool == "grep" and last_ok and not writes:
            return "Next: read_file on a match path or apply_patch — do not re-run same grep."
        if last_tool == "run_command" and last_ok:
            return "Next: fix findings with apply_patch, or run next AC verification — do not repeat same command."
        if readonly and not writes:
            return "Next: apply_patch/write_file toward AC, then lint/test run_command once."
        return "Next: one new tool toward AC completion; avoid duplicate reads/commands."

    if role == "QA Tester":
        return "Next: run_test/run_command if needed, then update_board when AC verified."

    if role == "Code Reviewer":
        return "Next: read_file/grep findings → apply_patch fixes or move board."

    return "Next: one concrete tool using prior output; no duplicate args."


def _format_do_not_repeat(successful_tool_keys: Sequence[Tuple[str, str]], *, limit: int = 8) -> List[str]:
    lines: List[str] = []
    seen: Set[str] = set()
    for name, args_json in successful_tool_keys:
        key = f"{name}|{args_json}"
        if key in seen:
            continue
        seen.add(key)
        try:
            args = json.loads(args_json)
            summary = summarize_tool_args(name, args if isinstance(args, dict) else {})
        except json.JSONDecodeError:
            summary = args_json[:60]
        lines.append(f"  - {name}({summary})")
        if len(lines) >= limit:
            lines.append("  … (more succeeded earlier this step)")
            break
    return lines


def build_step_recap_after_tools(
    *,
    agent_role: str,
    task: Optional[Dict[str, Any]],
    batch: Sequence[ToolBatchItem],
    tools_used_step: Set[str],
    successful_tool_keys: Sequence[Tuple[str, str]],
    iteration: int,
    max_iterations: int,
) -> str:
    lines = [STEP_RECAP_MARKER, f"LLM iteration {iteration}/{max_iterations} complete — tool batch summary."]
    if task:
        title = str(task.get("title") or "")
        if title:
            lines.append(f"Still working on: {title[:200]}")
    if batch:
        lines.append("This batch:")
        for tool_name, arguments, result in batch:
            ok = getattr(result, "success", False)
            dup = getattr(result, "duplicate_skip", False)
            status = "skip" if dup else ("ok" if ok else "FAIL")
            args = arguments if isinstance(arguments, dict) else {}
            lines.append(f"  • {tool_name}({summarize_tool_args(tool_name, args)[:70]}) → {status}")
            lines.append(f"    {_tool_intent_line(tool_name, args)}")
    dup_lines = _format_do_not_repeat(successful_tool_keys)
    if dup_lines:
        lines.append("Do NOT repeat these successful calls this step:")
        lines.extend(dup_lines)
    last_tool, last_ok = "", False
    if batch:
        last_tool = batch[-1][0]
        last_ok = bool(getattr(batch[-1][2], "success", False))
    lines.append(
        _suggest_next_action(
            agent_role,
            tools_used_step=tools_used_step,
            last_tool=last_tool,
            last_ok=last_ok,
        )
    )
    lines.append("Full tool output is in tool messages above; act on it now.")
    text = "\n".join(lines)
    return text[:2400] + ("…" if len(text) > 2400 else "")


def append_step_goal_anchor_if_enabled(
    messages: List[Dict[str, Any]],
    *,
    agent_role: str,
    task_id: Optional[str],
) -> None:
    from backend.services.workflow_settings import get_workflow_settings

    if not step_recap_enabled(get_workflow_settings()):
        return
    if not task_id:
        return
    task = find_task_by_id(task_id)
    if not task:
        return
    block = build_step_goal_anchor(agent_role, task)
    if block:
        messages.append({"role": "system", "content": block})


def append_step_recap_after_batch_if_enabled(
    messages: List[Dict[str, Any]],
    *,
    agent_role: str,
    task_id: Optional[str],
    batch: Sequence[ToolBatchItem],
    tools_used_step: Set[str],
    successful_tool_keys: Sequence[Tuple[str, str]],
    iteration: int,
    max_iterations: int,
) -> None:
    from backend.services.workflow_settings import get_workflow_settings

    if not step_recap_enabled(get_workflow_settings()):
        return
    task = find_task_by_id(task_id) if task_id else None
    block = build_step_recap_after_tools(
        agent_role=agent_role,
        task=task,
        batch=batch,
        tools_used_step=tools_used_step,
        successful_tool_keys=successful_tool_keys,
        iteration=iteration,
        max_iterations=max_iterations,
    )
    if block:
        messages.append({"role": "system", "content": block})
