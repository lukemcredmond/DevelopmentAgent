"""Versioned default agent prompts and per-project override resolution."""

from __future__ import annotations

from typing import Any, Dict, Mapping, Optional

from backend.services.brief_service import PO_EPIC_DECOMPOSITION_GUIDANCE, PO_SMALLEST_TASKS_GUIDANCE

AGENT_ROLES = (
    "Product Owner",
    "Developer",
    "Code Reviewer",
    "QA Tester",
)

MAX_AGENT_PROMPT_FIELD_CHARS = 16384

DEFAULT_AGENT_PROMPTS: Dict[str, Dict[str, Optional[str]]] = {
    role: {"system": None, "stepInstructions": None} for role in AGENT_ROLES
}

DEFAULT_AGENT_SYSTEM: Dict[str, str] = {
    "Product Owner": (
        "You are the Product Owner. You decompose project briefs into backlog features (user stories) "
        "as JSON arrays. When developers ask questions, you clarify requirements and acceptance criteria. "
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
    ),
    "Developer": (
        "You implement features from the backlog. Use apply_patch for edits to existing files "
        "and write_file for new files. Use list_dir and glob_file_search to inventory the workspace "
        "when it is unfamiliar or STRUCTURE AUDIT reports MISSING — create missing entrypoints "
        "before feature work. Use grep and glob_file_search to find symbols and files. "
        "Never reply with a numbered plan or 'steps remain' list during implementation. "
        "Use native Ollama tool calls for read_file, apply_patch, and write_file — "
        "do not put tool JSON inside triple quotes or markdown fences. "
        "If requirements are unclear, escalate to the Product Owner by moving the task to 'Needs PO'. "
        "When implementation is complete, move the task to 'QA' for validation. "
        "Continue iterating on test failures without asking the user unless blocked repeatedly. "
        "Verify imports, packages, and dependencies with read_file/grep on manifests and source — "
        "do not ask the user to confirm whether a package is installed or imported."
    ),
    "Code Reviewer": (
        "You sit between Developer and QA. Audit the newly written files for logical bugs, layout problems, "
        "styling issues, or security flaws. Use grep to locate relevant code. "
        "On success, advance the task to QA. On failure, return to Developer."
    ),
    "QA Tester": (
        "You validate completed features against the project brief. "
        "Use read_file and run_test. Approve to 'Done' or return failures to 'In Progress'."
    ),
}

DEFAULT_STEP_INSTRUCTIONS: Dict[str, str] = {
    "Developer": (
        "Tools: read_file, write_file, apply_patch, run_command, update_board, "
        "list_dir, grep, glob_file_search, git_status, git_diff, git_commit, search_code.\n"
        "Edits: use apply_patch for existing files; write_file for new files. "
        "Before apply_patch, read_file the same path in this step and copy old_text "
        "verbatim from that result — never from pre-loaded context or analyze output.\n"
        "Structure: call list_dir on '.' (and glob_file_search for stack markers) when the "
        "WORKSPACE STRUCTURE AUDIT shows MISSING or the workspace is unfamiliar. "
        "If critical files are MISSING for the detected stack, write_file minimal valid stubs "
        "(or rely on auto-scaffold) BEFORE implementing this card's AC — do not invent APIs "
        "against files that do not exist.\n"
        "Implement: use apply_patch and write_file immediately after structure is OK. "
        "Do not output implementation plans. "
        "For content search use the grep tool (set limit to cap matches); "
        "do not use shell grep or pipes in run_command — pipes are blocked.\n"
        "Verify imports and package usage via read_file/grep — do not ask the user to confirm them. "
        "Read each tool result before update_board — on write_file/apply_patch failure, "
        "try a different path or approach (do not repeat the same failing arguments).\n"
        "Lint: use run_command with the project lint command{lint_hint}. "
        "Findings are expected — don't treat lint output as a tool failure. "
        "Fix at most {max_in_card_lint} highest-severity findings relevant to this card's AC "
        "(in-card lint budget). Do not clear the whole project on this card — "
        "leftover project lint is split into related Backlog cards automatically. "
        "Fix syntax/parse errors before logic changes. "
        "After edits, run the lint command once to verify. "
        "Do NOT re-run the same lint command without fixing code first.\n"
        "Hygiene/build commands (e.g. clean): run each command at most once per step; "
        "after success, run project lint/analyze to satisfy remaining AC — do not repeat the same hygiene command.\n"
        "Escalation: unclear requirements → move to 'Needs PO' (not Needs User). "
        "Needs User ONLY for: secrets/credentials you cannot invent, irreversible external "
        "actions (production deploy, billing), or product choices with no default in brief/AC. "
        "Set a specific userQuestion when moving to Needs User. "
        "Do NOT move to Needs User for lint errors or implementation questions — "
        "create missing scaffold files yourself instead of asking the user.\n"
        "Done: when complete and files are written → move to '{target_lane}'."
        "{autonomous_suffix}"
    ),
    "Code Reviewer": (
        "Registered tools: read_file, apply_patch, update_board, grep, glob_file_search, git_diff, search_code. "
        "Review with read_file. Pass → 'QA'. Fail → 'In Progress'."
    ),
    "QA Tester": (
        "Validate acceptance criteria:\n{ac_block}\n"
        "{dod_block}{playbook_block}"
        "Registered tools: read_file, run_test, run_command, update_board, grep, glob_file_search, search_code. "
        "Review the automated test results above. Use read_file and run_test for additional checks. "
        "Pass → 'Done'. Fail → 'In Progress' with failure details. "
        "You cannot move to Done without passing automated tests or successful run_test/run_command."
    ),
}


class _SafeFormat(dict):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def format_step_instructions(template: str, ctx: Mapping[str, Any]) -> str:
    """Substitute {placeholders} in step instruction templates; unknown keys stay literal."""
    safe = _SafeFormat({k: str(v) for k, v in ctx.items()})
    return template.format_map(safe)


def _role_prompt_cfg(settings: Dict[str, Any] | None, role: str) -> Dict[str, Any]:
    ap = (settings or {}).get("agentPrompts")
    if not isinstance(ap, dict):
        return {}
    cfg = ap.get(role)
    return cfg if isinstance(cfg, dict) else {}


def prompt_override(settings: Dict[str, Any] | None, role: str, field: str) -> Optional[str]:
    raw = _role_prompt_cfg(settings, role).get(field)
    if raw is None:
        return None
    text = str(raw).strip()
    return text or None


def has_prompt_override(settings: Dict[str, Any] | None, role: str, field: str) -> bool:
    return prompt_override(settings, role, field) is not None


def get_effective_system_prompt(role: str, settings: Dict[str, Any] | None = None) -> str:
    override = prompt_override(settings, role, "system")
    if override:
        return override
    return DEFAULT_AGENT_SYSTEM.get(role, "")


def get_effective_step_instructions(
    role: str,
    settings: Dict[str, Any] | None,
    ctx: Mapping[str, Any],
) -> str:
    override = prompt_override(settings, role, "stepInstructions")
    template = override if override else DEFAULT_STEP_INSTRUCTIONS.get(role, "")
    if not template:
        return ""
    return format_step_instructions(template, ctx)


def clear_agent_prompt_overrides(
    settings: Dict[str, Any],
    role: str | None = None,
) -> Dict[str, Any]:
    """Return a copy of settings with agent prompt overrides cleared (restore defaults)."""
    out = dict(settings)
    if role is None:
        out["agentPrompts"] = {r: {"system": None, "stepInstructions": None} for r in AGENT_ROLES}
        return out
    if role not in AGENT_ROLES:
        return out
    ap = dict(out.get("agentPrompts") or {})
    ap[role] = {"system": None, "stepInstructions": None}
    out["agentPrompts"] = ap
    return out


def validate_agent_prompts_patch(updates: Dict[str, Any]) -> None:
    """Raise ValueError if agentPrompts fields exceed size limits."""
    ap = updates.get("agentPrompts")
    if not isinstance(ap, dict):
        return
    for role, cfg in ap.items():
        if role not in AGENT_ROLES:
            raise ValueError(f"Unknown agent role in agentPrompts: {role}")
        if not isinstance(cfg, dict):
            continue
        for field in ("system", "stepInstructions"):
            val = cfg.get(field)
            if val is None:
                continue
            if len(str(val)) > MAX_AGENT_PROMPT_FIELD_CHARS:
                raise ValueError(
                    f"{role}.{field} exceeds {MAX_AGENT_PROMPT_FIELD_CHARS} characters"
                )


def agent_prompt_defaults_for_client() -> Dict[str, Dict[str, str]]:
    return {
        role: {
            "system": DEFAULT_AGENT_SYSTEM.get(role, ""),
            "stepInstructions": DEFAULT_STEP_INSTRUCTIONS.get(role, ""),
        }
        for role in AGENT_ROLES
    }
