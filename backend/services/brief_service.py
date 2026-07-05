"""Project brief helpers — append user features and keep PO context in sync."""

from backend import state
from backend.services.project_service import save_current_project_state

WORKFLOW_LANES = [
    "Backlog",
    "Pending Approval",
    "In Progress",
    "Needs PO",
    "Needs User",
    "Code Review",
    "QA",
    "Done",
]

PO_SMALLEST_TASKS_GUIDANCE = (
    "Always break work into the smallest achievable backlog items — each card should be "
    "completable in one focused dev pass with testable acceptance criteria. "
    "Prefer many small cards over few large ones. "
    "On every add_backlog_tasks entry set workType (planning|implementation|review|qa|user_action), "
    "requiresDev (true/false), and requiresQa (true/false). "
    "Planning/decomposition cards must have workType=planning and requiresDev=false."
)


def append_feature_to_brief(title: str, description: str, source: str = "user") -> str:
    """Appends a user feature request to the persisted project brief."""
    entry = f"- {title}: {description}"
    if state.PROJECT_BRIEF.strip():
        state.PROJECT_BRIEF = f"{state.PROJECT_BRIEF.rstrip()}\n\n{entry}"
    else:
        state.PROJECT_BRIEF = entry
    save_current_project_state()
    record_brief_changelog(source, f"Added feature: {title}", entry)
    return state.PROJECT_BRIEF


def append_brief_text(text: str, source: str, summary: str) -> str:
    if not text.strip():
        return state.PROJECT_BRIEF
    if state.PROJECT_BRIEF.strip():
        state.PROJECT_BRIEF = f"{state.PROJECT_BRIEF.rstrip()}\n\n{text.strip()}"
    else:
        state.PROJECT_BRIEF = text.strip()
    save_current_project_state()
    record_brief_changelog(source, summary, text[:300])
    return state.PROJECT_BRIEF


def set_project_brief(brief: str, source: str = "user") -> None:
    if brief != state.PROJECT_BRIEF:
        record_brief_changelog(source, "Project brief updated", brief[:300])
    state.PROJECT_BRIEF = brief
    save_current_project_state()


def record_brief_changelog(source: str, summary: str, snippet: str = "") -> None:
    state.storage.add_brief_changelog(
        state.CURRENT_PROJECT_ID,
        source=source,
        summary=summary,
        snippet=snippet[:500],
    )


def existing_backlog_titles() -> list[str]:
    titles: list[str] = []
    for lane in ("Backlog", "Pending Approval"):
        for task in state.SHARED_BOARD.get(lane, []):
            title = task.get("title")
            if title:
                titles.append(title)
    return titles
