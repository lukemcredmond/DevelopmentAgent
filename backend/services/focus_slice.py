"""Focus slices (single AC / subtask) for Dev micro-steps and prompt section bundles."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from backend.agents.task_context import (
    coerce_task_text,
    find_task_by_id,
    is_task_done,
    normalize_task,
)
from backend.services.prompt_sections import FocusContext
from backend.services.workflow_settings import get_workflow_settings


def _ac_list(task: Dict[str, Any]) -> List[str]:
    return [str(c).strip() for c in (task.get("acceptanceCriteria") or []) if str(c).strip()]


def default_focus_mode(task: Dict[str, Any]) -> str:
    acs = _ac_list(task)
    if len(acs) >= 2:
        return "ac"
    subtasks = [s for s in (task.get("subtaskIds") or []) if s]
    if subtasks:
        return "subtask"
    return "whole"


def ensure_focus_initialized(task: Dict[str, Any]) -> None:
    normalize_task(task)
    mode = str(task.get("focusMode") or "").strip().lower()
    if mode not in ("ac", "subtask", "whole"):
        task["focusMode"] = default_focus_mode(task)
        mode = task["focusMode"]
    if mode == "ac":
        acs = _ac_list(task)
        if not acs:
            task["focusMode"] = "whole"
        else:
            idx = task.get("focusAcIndex")
            if not isinstance(idx, (int, float)):
                task["focusAcIndex"] = 0
            else:
                task["focusAcIndex"] = max(0, min(int(idx), len(acs) - 1))
    elif mode == "subtask":
        if not task.get("focusSubtaskId") and task.get("subtaskIds"):
            for sid in task["subtaskIds"]:
                sub = find_task_by_id(str(sid))
                if sub and not is_task_done(str(sid)):
                    task["focusSubtaskId"] = str(sid)
                    break
    if "focusStepsRun" not in task or not isinstance(task.get("focusStepsRun"), (int, float)):
        task["focusStepsRun"] = 0
    if task.get("focusPackPaths") is not None and not isinstance(task.get("focusPackPaths"), list):
        task["focusPackPaths"] = []
    elif "focusPackPaths" not in task:
        task["focusPackPaths"] = []


def focus_context_from_task(task: Dict[str, Any], agent_role: Optional[str] = None) -> FocusContext:
    ensure_focus_initialized(task)
    role = (agent_role or "").strip()
    mode = str(task.get("focusMode") or "whole")
    ac_idx: Optional[int] = None
    sub_id: Optional[str] = None
    if mode == "ac":
        ac_idx = int(task.get("focusAcIndex") or 0)
    elif mode == "subtask":
        sub_id = str(task.get("focusSubtaskId") or "") or None
    include_full = mode == "whole" or role in ("Code Reviewer", "QA Tester", "Product Owner")
    return FocusContext(
        agent_role=role,
        focus_mode=mode,
        ac_index=ac_idx,
        subtask_id=sub_id,
        include_full_spec=include_full,
    )


def dev_micro_steps_enabled(task: Dict[str, Any]) -> bool:
    ws = get_workflow_settings()
    if not ws.get("enableFocusMicroSteps", True):
        return False
    wt = str(task.get("workType") or "implementation").lower()
    if wt != "implementation":
        return False
    if not task.get("requiresDev", True):
        return False
    mode = str(task.get("focusMode") or default_focus_mode(task))
    if mode == "whole":
        acs = _ac_list(task)
        return len(acs) > 1
    return mode in ("ac", "subtask")


def all_focus_slices_done(task: Dict[str, Any]) -> bool:
    ensure_focus_initialized(task)
    mode = str(task.get("focusMode") or "whole")
    if mode == "whole":
        return True
    if mode == "ac":
        acs = _ac_list(task)
        if not acs:
            return True
        idx = int(task.get("focusAcIndex") or 0)
        checklist = task.get("acChecklist") or []
        if idx >= len(acs) - 1:
            if isinstance(checklist, list) and len(checklist) >= len(acs):
                return all(bool(x) for x in checklist[: len(acs)])
            return idx >= len(acs) - 1
        return False
    if mode == "subtask":
        for sid in task.get("subtaskIds") or []:
            if not is_task_done(str(sid)):
                return False
        return True
    return True


def focus_cap_reached(task: Dict[str, Any]) -> bool:
    ws = get_workflow_settings()
    cap = int(ws.get("maxFocusStepsPerCard", 8) or 8)
    return int(task.get("focusStepsRun") or 0) >= cap


def micro_step_complete(task: Dict[str, Any], agent_result: str) -> bool:
    """Heuristic: explicit focus_done decision or AC checklist tick for current index."""
    for d in reversed(task.get("decisions") or []):
        if not isinstance(d, dict):
            continue
        if str(d.get("type") or "").lower() == "focus_done":
            return True
    result_lower = (agent_result or "").lower()
    if "focus_done" in result_lower or "criterion satisfied" in result_lower:
        return True
    mode = str(task.get("focusMode") or "whole")
    if mode == "ac":
        idx = int(task.get("focusAcIndex") or 0)
        checklist = task.get("acChecklist") or []
        if isinstance(checklist, list) and idx < len(checklist) and checklist[idx]:
            return True
    return False


def advance_focus(task: Dict[str, Any]) -> bool:
    """Move to next AC/subtask slice. Returns False if no further slice."""
    ensure_focus_initialized(task)
    mode = str(task.get("focusMode") or "whole")
    if mode == "ac":
        acs = _ac_list(task)
        if not acs:
            return False
        idx = int(task.get("focusAcIndex") or 0)
        if idx + 1 >= len(acs):
            return False
        task["focusAcIndex"] = idx + 1
        task["focusSpecSummary"] = None
        return True
    if mode == "subtask":
        subs = [str(s) for s in (task.get("subtaskIds") or [])]
        current = str(task.get("focusSubtaskId") or "")
        for i, sid in enumerate(subs):
            if sid == current and i + 1 < len(subs):
                task["focusSubtaskId"] = subs[i + 1]
                task["focusSpecSummary"] = None
                return True
        return False
    return False


def should_block_lane_advance_for_focus(task: Dict[str, Any]) -> bool:
    if not dev_micro_steps_enabled(task):
        return False
    return not all_focus_slices_done(task)


def focus_advance_after_step(task: Dict[str, Any], agent_result: str) -> None:
    task["focusStepsRun"] = int(task.get("focusStepsRun") or 0) + 1
    summary_line = _focus_summary_line(task)
    if summary_line:
        from backend.services.task_working_context import append_working_context

        append_working_context(task, kind="focus", summary=summary_line)
    if micro_step_complete(task, agent_result):
        advanced = advance_focus(task)
        if advanced:
            from datetime import datetime

            task["lastFocusSliceCompletedAt"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if focus_cap_reached(task) and not all_focus_slices_done(task):
        task["focusRecoveryRequired"] = True
        task["focusRecoveryReason"] = (
            f"Focus step cap reached ({task.get('focusStepsRun')}); unfinished slices remain"
        )


def reset_focus(task: Dict[str, Any]) -> None:
    task["focusMode"] = default_focus_mode(task)
    task["focusAcIndex"] = 0
    task["focusSubtaskId"] = None
    task["focusStepsRun"] = 0
    task["focusSpecSummary"] = None
    ensure_focus_initialized(task)


def _focus_summary_line(task: Dict[str, Any]) -> str:
    mode = str(task.get("focusMode") or "whole")
    if mode == "ac":
        acs = _ac_list(task)
        idx = int(task.get("focusAcIndex") or 0)
        if acs and idx < len(acs):
            return f"Focus step: AC {idx + 1}/{len(acs)} — {acs[idx][:120]}"
    if mode == "subtask" and task.get("focusSubtaskId"):
        sub = find_task_by_id(str(task["focusSubtaskId"]))
        title = sub.get("title", "?") if sub else "?"
        return f"Focus step: subtask {task['focusSubtaskId']} — {title[:120]}"
    return ""


def focus_log_label(task: Dict[str, Any]) -> str:
    mode = str(task.get("focusMode") or "whole")
    if mode == "ac":
        acs = _ac_list(task)
        idx = int(task.get("focusAcIndex") or 0)
        if acs:
            return f"Focus: AC {idx + 1}/{len(acs)}"
    if mode == "subtask" and task.get("focusSubtaskId"):
        return f"Focus: subtask {task['focusSubtaskId']}"
    return ""


_MICRO_STEP_SECTIONS = [
    "brief_slice",
    "card_core",
    "ac_focus",
    "scope_focus",
    "user_story",
    "task_spec_summary",
    "dod",
    "working_context",
    "dependencies",
    "related_cards",
    "qa_failure",
    "last_outcome",
    "decisions_recent",
    "transcript_recent",
    "project_evidence",
    "workspace_file_list",
    "codebase_pack",
]

_ROTATION_BUNDLES: List[List[str]] = [
    ["card_core", "ac_focus", "working_context"],
    ["decisions_recent", "transcript_recent", "related_cards"],
    ["dependencies", "task_spec_summary", "project_evidence"],
]


def sections_for_focus(
    focus: FocusContext,
    *,
    phase: str = "micro_step",
    in_step_iter: int = 0,
) -> List[str]:
    if focus.include_full_spec or focus.focus_mode == "whole":
        from backend.services.prompt_sections import full_sections_for_role

        return full_sections_for_role(focus.agent_role, focus)
    if phase == "in_step_rotation":
        bundles = _ROTATION_BUNDLES
        if not bundles:
            return _MICRO_STEP_SECTIONS
        return list(bundles[in_step_iter % len(bundles)])
    return list(_MICRO_STEP_SECTIONS)


def prepare_in_step_rotation_blocks(
    task: Dict[str, Any],
    brief: str,
    *,
    agent_role: str,
    codebase_pack: str = "",
) -> Tuple[List[str], List[str]]:
    """Pre-compose rotation supplement blocks for execute_step."""
    from backend.services.prompt_sections import compose_prompt

    focus = focus_context_from_task(task, agent_role)
    ws = get_workflow_settings()
    if not ws.get("enablePromptSectionRotation", True):
        return [], []
    if not dev_micro_steps_enabled(task):
        return [], []
    names: List[str] = []
    blocks: List[str] = []
    for i in range(len(_ROTATION_BUNDLES)):
        sec_ids = sections_for_focus(focus, phase="in_step_rotation", in_step_iter=i)
        block = compose_prompt(
            task,
            brief,
            sec_ids,
            focus,
            agent_role=agent_role,
            codebase_pack=codebase_pack,
        )
        if block.strip():
            names.append(f"bundle_{i}")
            blocks.append(block)
    return blocks, names


def default_pack_paths(task: Dict[str, Any]) -> List[str]:
    paths: List[str] = []
    for entry in task.get("focusPackPaths") or []:
        if entry:
            paths.append(str(entry))
    for f in task.get("files") or []:
        if isinstance(f, str):
            paths.append(f)
        elif isinstance(f, dict) and f.get("path"):
            paths.append(str(f["path"]))
    scope = coerce_task_text(task.get("scope") or "")
    for line in scope.splitlines():
        line = line.strip().lstrip("-*").strip()
        if line and ("/" in line or "*" in line or "." in line):
            paths.append(line)
    return list(dict.fromkeys(paths))
