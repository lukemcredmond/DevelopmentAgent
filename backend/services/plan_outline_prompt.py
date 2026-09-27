"""PO prompts for handoff-grade plan outlines."""

from __future__ import annotations

PLAN_OUTLINE_NEGATIVE_EXAMPLES = (
    "Do NOT return JSON task specs with description/acceptanceCriteria.\n"
    "Do NOT echo these instructions.\n"
    "Do NOT return epics JSON — markdown only.\n"
)


def build_plan_outline_explore_prompt(brief_text: str, *, planning_guidance: str, dod_block: str) -> str:
    return (
        "Optional workspace reconnaissance ONLY — you will write the full plan in the next step.\n"
        "You may use at most 2–3 readonly tool calls (list_dir, glob_file_search, read_file, grep).\n"
        "Do NOT use semantic_search or graph_query.\n"
        "For greenfield briefs, skip tools unless pubspec.yaml or package.json exists.\n"
        "If you already know enough from the brief, reply with a one-line note: "
        '"Ready to draft plan from brief."\n'
        f"{planning_guidance}"
        f"{dod_block}\nProject brief:\n{brief_text}"
    )


def build_plan_outline_write_prompt(
    brief_text: str,
    *,
    planning_guidance: str,
    dod_block: str,
    explore_notes: str = "",
) -> str:
    notes_block = ""
    if explore_notes.strip():
        notes_block = f"\nWorkspace notes from reconnaissance:\n{explore_notes.strip()}\n\n"
    return build_plan_outline_step_prompt(
        brief_text,
        planning_guidance=planning_guidance,
        dod_block=dod_block,
        preamble=(
            "Do NOT call any tools. Output the complete markdown plan now.\n" + notes_block
        ),
    )


def build_plan_outline_step_prompt(
    brief_text: str,
    *,
    planning_guidance: str,
    dod_block: str,
    preamble: str = "",
) -> str:
    lead = preamble or (
        "Produce a handoff-grade markdown project plan ONLY — no JSON, no XML tool tags.\n"
        "If you need to inspect the workspace, use native tool calls (list_dir, glob_file_search, read_file), "
        "then reply with the markdown plan.\n"
    )
    return (
        f"{lead}"
        f"{PLAN_OUTLINE_NEGATIVE_EXAMPLES}"
        "Required sections (use exact ## headers):\n"
        "1. ## Summary — product one-liner + numbered core user loop.\n"
        "2. ## Approach — stack, offline/NFR constraints, persistence (tables OK).\n"
        "3. ## Data model — entities + export/import schema (code blocks allowed here only).\n"
        "4. ## Flows — primary screens / user journeys.\n"
        "5. ## Errors and edge cases — codes + user-visible messages (table OK).\n"
        "6. ## Testing — test layers + must-run cases tied to the brief.\n"
        "7. ## Risks\n"
        "8. ## Open questions\n"
        "9. ## Proposed epics — 6–12 bullets: “Title — capability + why”.\n"
        "10. ## Epic details — for EACH proposed epic, a ### Epic: <title> block with:\n"
        "    - **Scope** (bullets)\n"
        "    - **Out of scope**\n"
        "    - **Suggested child cards** (3–6 one-line titles)\n"
        "    - **AC hints** (testable, implementation-sized)\n"
        "    - **Test notes** (commands or manual checks)\n"
        f"{planning_guidance}"
        f"{dod_block}\nProject brief:\n{brief_text}"
    )


def build_plan_outline_refine_prompt(outline: str, issues_text: str) -> str:
    return (
        "Do NOT call any tools.\n"
        "Fix ONLY structure and missing sections in this plan. Preserve all facts and decisions.\n"
        "Output the FULL corrected markdown plan using the same required ## sections "
        "(including ## Epic details with ### Epic: blocks).\n"
        f"{PLAN_OUTLINE_NEGATIVE_EXAMPLES}"
        f"Quality issues to fix:\n{issues_text}\n\n"
        f"Current draft:\n{outline}"
    )
