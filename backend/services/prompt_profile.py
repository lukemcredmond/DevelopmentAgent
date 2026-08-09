"""Workflow prompt profile: full vs local_slm (lean static prompts for 7B–14B / 12GB VRAM)."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from backend.services.brief_service import (
    PO_EPIC_DECOMPOSITION_GUIDANCE,
    PO_LOCAL_SLM_GUIDANCE,
    PO_SMALLEST_TASKS_GUIDANCE,
)

PROMPT_PROFILE_FULL = "full"
PROMPT_PROFILE_LOCAL_SLM = "local_slm"

LOCAL_SLM_SYSTEM: Dict[str, str] = {
    "Product Owner": (
        "You are the Product Owner. Break requests into small, testable developer tasks.\n"
        "### Core rules\n"
        "1. Decompose epics: focused user-facing epics (about 6–12 for a standard feature).\n"
        "2. Small cards only: 1–3 testable acceptance criteria per dev card; split with add_backlog_tasks if more.\n"
        "3. No duplicate work: grep/glob_file_search the board/codebase; reuse existing cards.\n"
        "4. Use tools directly (update_board, add_backlog_tasks, add_subtasks) — never tell the user to run tools.\n"
        "5. Task Detail + tool messages in this chat hold the brief and exploration output — use them.\n"
        f"{PO_LOCAL_SLM_GUIDANCE}"
    ),
    "Developer": (
        "You implement backlog cards. Use read_file then apply_patch/write_file. "
        "Use list_dir, grep, glob_file_search to explore. "
        "No plan-only text — call tools. Unclear requirements → Needs PO. Done → QA. "
        "Use native tool calls only (not JSON in markdown fences)."
    ),
    "Code Reviewer": (
        "Review changed code with read_file and grep. Pass → QA. Fail → In Progress."
    ),
    "QA Tester": (
        "Validate acceptance criteria with read_file, run_test, run_command. Pass → Done. Fail → In Progress."
    ),
}

LOCAL_SLM_STEP_INSTRUCTIONS: Dict[str, str] = {
    "Developer": (
        "Tools: {registered_tools}.\n"
        "read_file before apply_patch (same step); copy old_text from tool output.\n"
        "Implement with apply_patch/write_file — no numbered plans.\n"
        "grep for search; run_command for lint/test per AC (once per command unless code changed).\n"
        "Fix up to {max_in_card_lint} AC-relevant lint issues. Needs PO if requirements unclear.\n"
        "Done → move to '{target_lane}'."
        "{autonomous_suffix}"
    ),
    "Code Reviewer": (
        "Tools: {registered_tools}. read_file → pass QA or fail In Progress."
    ),
    "QA Tester": (
        "AC:\n{ac_block}\n"
        "{dod_block}{playbook_block}"
        "Tools: {registered_tools}. Verify AC; pass Done or fail In Progress."
    ),
}

LOCAL_SLM_SECTIONS: List[str] = [
    "brief_slice",
    "card_core",
    "ac_focus",
    "user_story",
    "qa_failure",
    "last_outcome",
    "working_context",
    "dependencies",
]


def get_prompt_profile(ws: Optional[Dict[str, Any]] = None) -> str:
    if ws is None:
        from backend.services.workflow_settings import get_workflow_settings

        ws = get_workflow_settings()
    # Efficiency high forces lean budgets (local_slm).
    try:
        from backend.services.agent_efficiency import efficiency_high

        if efficiency_high(ws):
            return PROMPT_PROFILE_LOCAL_SLM
    except Exception:
        pass
    raw = str(ws.get("promptProfile") or PROMPT_PROFILE_FULL).strip().lower()
    if raw in (PROMPT_PROFILE_LOCAL_SLM, "local", "slm", "lean"):
        return PROMPT_PROFILE_LOCAL_SLM
    return PROMPT_PROFILE_FULL


def is_local_slm_profile(ws: Optional[Dict[str, Any]] = None) -> bool:
    return get_prompt_profile(ws) == PROMPT_PROFILE_LOCAL_SLM


def local_slm_sprint_preload_enabled(ws: Optional[Dict[str, Any]] = None) -> bool:
    """Full profile always preloads; local_slm respects localSlmSprintPreload (default on)."""
    if ws is None:
        from backend.services.workflow_settings import get_workflow_settings

        ws = get_workflow_settings()
    if not is_local_slm_profile(ws):
        return True
    return bool(ws.get("localSlmSprintPreload", True))


def po_planning_guidance_block() -> str:
    if is_local_slm_profile():
        return PO_LOCAL_SLM_GUIDANCE + "\n"
    return f"{PO_EPIC_DECOMPOSITION_GUIDANCE}\n\n{PO_SMALLEST_TASKS_GUIDANCE}\n\n"


def local_slm_sections_for_role(role: str, task: Dict[str, Any]) -> List[str]:
    sections = list(LOCAL_SLM_SECTIONS)
    blocked = task.get("blockedBy") or []
    if not blocked:
        sections = [s for s in sections if s != "dependencies"]
    return sections


def full_po_system_prompt() -> str:
    base = (
        "You are the Product Owner. You decompose project briefs into backlog features (user stories) "
        "as JSON arrays. When developers ask questions, you clarify requirements and acceptance criteria. "
        "During sprint steps the project brief and active card are already in Task Detail — never ask the "
        "user to paste the brief or restart onboarding. After list_dir, grep, or read_file, use the tool "
        "output in the conversation and call update_board, add_backlog_tasks, add_subtasks, or the JSON "
        "format requested in Task Detail. "
        "When the user adds features, refine them into clear developer-ready stories. "
        "Use update_board to move tasks from 'Needs PO' back to 'In Progress' when clarification is done. "
        "For cards in 'Refinement', answer developer questions, update AC/description, use add_backlog_tasks "
        "to split scope, then move to 'Backlog' when refinementComplete. "
        "Use add_backlog_tasks to add new stories to the Backlog; when splitting a large or stuck card, "
        "pass split_from_task_id so the original moves to Done with a split note. "
        "Use add_subtasks for ordered child todos under a parent card (during refinement set executionOrder). "
        "Invoke add_backlog_tasks yourself — never instruct the user to call it. "
        "If a card already covers the same request, do not recreate it — reuse that card and its outcomes. "
        "Prefer acting (split, move board) over asking clarifying questions when acceptance criteria exist. "
        "Use grep and glob_file_search to explore the codebase; prefer grep over search_code for patterns. "
        "When planning Features (epics), prefer many focused product epics with multiple small children — "
        "not a handful of audit/meta mega-epics. "
        f"{PO_EPIC_DECOMPOSITION_GUIDANCE} "
        f"{PO_SMALLEST_TASKS_GUIDANCE}"
    )
    return base
