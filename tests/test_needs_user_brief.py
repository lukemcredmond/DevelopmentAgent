"""Structured Needs User question / why / how-to-unblock copy."""

from backend.bootstrap import initialize
from backend.services.needs_user_guard import (
    apply_needs_user_brief,
    build_needs_user_brief,
    looks_generic_needs_user_text,
)


def test_generic_po_round_text_detected():
    assert looks_generic_needs_user_text(
        "PO and Dev could not agree after 3 rounds — please clarify requirements."
    )
    assert not looks_generic_needs_user_text(
        "Which OAuth provider should login use, Google or Apple?"
    )


def test_po_limit_empty_ac_asks_for_criteria_not_round_count():
    task = {
        "id": "T-PO-LIM",
        "title": "Club Card Display UI",
        "description": "vague",
        "acceptanceCriteria": [],
        "poRoundTrips": 3,
    }
    generic = "PO and Dev could not agree after 3 rounds — please clarify requirements."
    brief = build_needs_user_brief(task, kind="po_limit", raw_msg=generic)
    assert "could not agree" not in brief["question"].lower()
    assert "acceptance criteria" in brief["question"].lower()
    assert "send to developer" in brief["action"].lower()
    assert brief["why"] != brief["action"]
    assert brief["question"] != brief["action"]
    assert "3" in brief["why"]  # round count is a footnote, not the ask
    assert brief["suggestedTarget"] == "dev"


def test_lint_brief_names_file_and_dev_button():
    task = {
        "id": "T-LINT",
        "title": "Fix store UI",
        "description": "Store aisle management screen with filters and sorting.",
        "acceptanceCriteria": ["List aisles", "Filter by name"],
        "lastCommandDiagnostics": [
            {
                "file": "lib/aisle.dart",
                "line": 42,
                "message": "unused import",
                "severity": "warning",
            }
        ],
    }
    brief = build_needs_user_brief(task, kind="stuck_loop", raw_msg="Agents made no progress.")
    blob = f"{brief['question']} {brief['why']} {brief['action']}".lower()
    assert "lib/aisle.dart" in blob
    assert "send to developer" in brief["action"].lower()
    assert "product owner" in brief["action"].lower()
    assert brief["kind"] == "lint"


def test_explore_brief_asks_for_first_file():
    task = {
        "id": "T-EX",
        "title": "Club Card Image Storage",
        "description": "Store card images in object storage with a public URL.",
        "acceptanceCriteria": ["Upload image", "Show URL"],
        "lastStepOutcome": {"exitReason": "explore_budget_exhausted"},
    }
    brief = build_needs_user_brief(task, kind="stuck_loop", raw_msg="")
    assert brief["kind"] == "explore"
    assert "file" in brief["question"].lower()
    assert "send to developer" in brief["action"].lower()


def test_specific_agent_question_preserved():
    task = {"id": "T-Q", "title": "Auth", "description": "Add login", "acceptanceCriteria": ["OAuth"]}
    raw = "Needs User: Which OAuth provider should we use?"
    brief = build_needs_user_brief(task, kind="dev_board_move", raw_msg=raw)
    assert "oauth" in brief["question"].lower()
    apply_needs_user_brief(task, brief)
    assert task["userQuestion"] == brief["question"]
    assert task["needsUserReason"] != task["needsUserAction"]
    assert task["needsUserSuggestedTarget"] == "dev"


def test_try_move_to_needs_user_persists_distinct_fields():
    initialize()
    from backend import state
    from backend.agents.task_context import get_task_lane, init_new_task
    from backend.services.sprint_service import _try_move_to_needs_user
    from backend.services.workflow_settings import save_workflow_settings

    save_workflow_settings({"maxPoRoundTrips": 3, "autonomousMode": False})
    task = init_new_task(
        {
            "id": "T-NU-BRIEF",
            "title": "Club Card Display UI",
            "description": "vague",
            "status": "In Progress",
            "acceptanceCriteria": [],
        }
    )
    task["poRoundTrips"] = 3
    state.SHARED_BOARD = {
        "Backlog": [],
        "In Progress": [task],
        "Needs PO": [],
        "Needs User": [],
        "QA": [],
        "Done": [],
    }
    ok = _try_move_to_needs_user(
        task["id"],
        task,
        "PO and Dev could not agree after 3 rounds — please clarify requirements.",
        kind="po_limit",
    )
    assert ok is True
    assert get_task_lane("T-NU-BRIEF") == "Needs User"
    assert "acceptance criteria" in (task.get("userQuestion") or "").lower()
    assert task.get("needsUserReason") != task.get("needsUserAction")
    assert "send to developer" in (task.get("needsUserAction") or "").lower()
    assert task.get("needsUserKind") == "po_limit"
