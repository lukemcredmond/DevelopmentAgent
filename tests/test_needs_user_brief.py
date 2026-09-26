"""Structured Needs User question / why / how-to-unblock copy."""

from backend.bootstrap import initialize
from backend.services.needs_user_guard import (
    apply_needs_user_brief,
    build_needs_user_brief,
    looks_generic_needs_user_text,
    should_escalate_to_needs_user,
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


def test_read_only_no_edits_classifies_as_explore():
    task = {
        "id": "T-RO",
        "title": "Implement Export to storage location — core flow",
        "description": "Serialize meals to JSON and save/share.",
        "acceptanceCriteria": ["Export from main menu", "Happy path test"],
        "lastStepOutcome": {
            "exitReason": "read_only_no_edits",
            "whyCardStayed": "Developer read files but never called apply_patch/write_file.",
        },
    }
    brief = build_needs_user_brief(task, kind="stuck_loop", raw_msg="")
    assert brief["kind"] == "explore"
    assert "file" in brief["question"].lower()
    blob = f"{brief['question']} {brief['why']} {brief['action']}".lower()
    assert "apply_patch" in blob or "read" in blob or "explore" in blob
    assert "send to developer" in brief["action"].lower()


def test_phase_cycle_cap_with_lint_classifies_as_phase_cycle_cap():
    task = {
        "id": "T-CAP",
        "title": "Create Store Form",
        "description": "Store creation screen with name persistence.",
        "acceptanceCriteria": ["Name required", "Persist store"],
        "phaseCycleCapReached": True,
        "forcePatchNextDevStep": True,
        "lastStepOutcome": {"exitReason": "explore_budget_exhausted"},
        "lastCommandDiagnostics": [
            {
                "file": "lib/missing.dart",
                "line": 1,
                "message": "Target of URI doesn't exist",
            }
        ],
    }
    brief = build_needs_user_brief(
        task,
        kind="phase_cycle_cap",
        raw_msg="Phase cycle cap reached. Split the card or reset the Developer visit latch.",
    )
    assert brief["kind"] == "phase_cycle_cap"
    blob = f"{brief['question']} {brief['why']}".lower()
    assert "lib/missing.dart" in blob or "uri" in blob
    assert brief.get("options")
    labels = [str(o.get("label") or "").lower() for o in brief["options"]]
    assert any("lib/missing.dart" in lab or "fix" in lab for lab in labels)
    assert labels[-1] == "other (type below)"
    assert labels.count("other (type below)") == 1


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


def test_move_board_stage_to_needs_user_rejects_explore_blocker():
    initialize()
    from backend import state
    from backend.agents.task_context import get_task_lane, init_new_task
    from backend.services.board_service import move_board_stage

    task = init_new_task(
        {
            "id": "T-NU-MOVE",
            "title": "Implement Export to storage location — core flow",
            "description": "Serialize meals to JSON and save/share.",
            "status": "In Progress",
            "acceptanceCriteria": ["Export from main menu"],
        }
    )
    task["lastStepOutcome"] = {
        "exitReason": "read_only_no_edits",
        "whyCardStayed": "Developer read files but never called apply_patch/write_file on Export.",
        "message": "Dev step read files but made no edits.",
    }
    state.SHARED_BOARD = {
        "Backlog": [],
        "In Progress": [task],
        "Needs PO": [],
        "Needs User": [],
        "QA": [],
        "Done": [],
    }
    result = move_board_stage("T-NU-MOVE", "Needs User")
    assert result.startswith("Error:")
    assert "autonomous" in result.lower() or "explore" in result.lower() or "autonomous_tool_blocker" in result
    assert get_task_lane("T-NU-MOVE") == "In Progress"
    assert not task.get("userQuestion")


def test_normalize_reconciles_misrouted_explore_needs_user():
    from backend.agents.task_context import normalize_task

    task = {
        "id": "T-NU-LOAD",
        "title": "Implement Export to storage location — core flow",
        "description": "Serialize meals to JSON.",
        "status": "Needs User",
        "acceptanceCriteria": ["Export from main menu"],
        "userQuestion": None,
        "needsUserReason": None,
        "needsUserAction": None,
        "needsUserKind": "explore",
        "lastStepOutcome": {
            "exitReason": "read_only_no_edits",
            "whyCardStayed": "Developer read files but never called apply_patch/write_file.",
        },
    }
    normalize_task(task)
    assert task.get("status") == "In Progress"
    assert task.get("forcePatchNextDevStep") is True
    assert not task.get("userQuestion")
    assert not task.get("needsUserKind")


def test_lint_escalation_blocked_by_guard():
    task = {
        "id": "T-LINT-GUARD",
        "title": "Fix UI",
        "lastCommandDiagnostics": [{"file": "lib/a.dart", "line": 1, "message": "err"}],
    }
    allowed, reason = should_escalate_to_needs_user(task, "Agents made no progress.", kind="stuck_loop")
    assert allowed is False
    assert reason == "lint_use_file_blocker"


def test_phase_cycle_cap_brief_stores_options():
    task = {
        "id": "T-OPT",
        "title": "Big card",
        "phaseCycleCapReached": True,
        "acceptanceCriteria": ["Done"],
    }
    brief = build_needs_user_brief(task, kind="phase_cycle_cap", raw_msg="Phase cycle cap reached.")
    apply_needs_user_brief(task, brief)
    assert isinstance(task.get("needsUserOptions"), list)
    assert len(task["needsUserOptions"]) >= 3
    assert task["needsUserOptions"][-1]["id"] == "other"


def test_lint_tool_blocked_from_needs_user_when_po_exhausted():
    initialize()
    from backend import state
    from backend.agents.task_context import get_task_lane, init_new_task
    from backend.services.sprint_service import _try_move_to_needs_user
    from backend.services.workflow_settings import save_workflow_settings

    save_workflow_settings({"maxPoRoundTrips": 3, "autonomousMode": False})
    task = init_new_task(
        {
            "id": "T-LINT-PO",
            "title": "Fix UI",
            "description": "Fix store screen",
            "status": "In Progress",
            "acceptanceCriteria": ["Works"],
            "lastCommandDiagnostics": [{"file": "lib/a.dart", "line": 1, "message": "err"}],
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
        "Agents made no progress — please clarify requirements.",
        kind="stuck_loop",
    )
    assert ok is False
    assert get_task_lane("T-LINT-PO") == "In Progress"
    assert task.get("forcePatchNextDevStep") is True
    assert not task.get("userQuestion")


def test_reconcile_moves_lint_kind_needs_user_back_to_in_progress():
    initialize()
    from backend import state
    from backend.agents.task_context import get_task_lane, init_new_task
    from backend.services.needs_user_guard import reconcile_autonomous_needs_user_cards

    task = init_new_task(
        {
            "id": "T-NU-LINT",
            "title": "Fix store UI",
            "description": "Store screen",
            "status": "Needs User",
            "acceptanceCriteria": ["Done"],
        }
    )
    task["needsUserKind"] = "lint"
    task["userQuestion"] = "Which lint error first?"
    task["needsUserReason"] = "Blocked on lint or tool failures"
    task["needsUserAction"] = "Reply then Send to Developer"
    task["lastCommandDiagnostics"] = [{"file": "lib/main.dart", "line": 1, "message": "unused import"}]
    state.SHARED_BOARD = {
        "Backlog": [],
        "In Progress": [],
        "Needs PO": [],
        "Needs User": [task],
        "QA": [],
        "Done": [],
    }
    result = reconcile_autonomous_needs_user_cards()
    assert result["count"] == 1
    assert get_task_lane("T-NU-LINT") == "In Progress"
    assert task.get("forcePatchNextDevStep") is True
    assert not task.get("userQuestion")
    assert not task.get("needsUserOptions")


def test_secret_brief_stores_mcq_options():
    task = {"id": "T-SEC", "title": "Auth", "description": "Add login", "acceptanceCriteria": ["OAuth"]}
    brief = build_needs_user_brief(task, kind="dev_board_move", raw_msg="Need production API key for OAuth")
    apply_needs_user_brief(task, brief)
    assert brief["kind"] == "secret"
    assert isinstance(task.get("needsUserOptions"), list)
    assert len(task["needsUserOptions"]) >= 2


def test_product_choice_brief_stores_mcq_options():
    task = {"id": "T-PC", "title": "Theme", "description": "Add theme toggle", "acceptanceCriteria": ["Toggle works"]}
    raw = "Needs User: Should the default theme be light mode or dark mode?"
    brief = build_needs_user_brief(task, kind="dev_board_move", raw_msg=raw)
    apply_needs_user_brief(task, brief)
    assert brief["kind"] == "product_choice"
    assert isinstance(task.get("needsUserOptions"), list)
    assert len(task["needsUserOptions"]) >= 2
    blob = " ".join(str(o.get("label") or "") for o in task["needsUserOptions"]).lower()
    assert "light" in blob
    assert "dark" in blob
    assert "simplest option" not in blob
    assert [o["id"] for o in task["needsUserOptions"]].count("other") == 1
    assert task["needsUserOptions"][-1]["id"] == "other"


def test_phase_cycle_cap_park_applies_mcq_to_live_board_task():
    initialize()
    from backend import state
    from backend.agents.task_context import find_task_by_id, get_task_lane, init_new_task
    from backend.services.sprint_service import _try_move_to_needs_user
    from backend.services.workflow_settings import save_workflow_settings

    save_workflow_settings({"pauseSprintOnNeedsUser": False, "autonomousMode": False})
    task = init_new_task(
        {
            "id": "T-LIVE-MCQ",
            "title": "Import from document",
            "description": "Add import flow",
            "status": "In Progress",
            "acceptanceCriteria": ["User can import a PDF"],
        }
    )
    task["phaseCycleCapReached"] = True
    state.SHARED_BOARD = {
        "Backlog": [],
        "In Progress": [task],
        "Needs PO": [],
        "Needs User": [],
        "QA": [],
        "Done": [],
    }
    task_copy = dict(task)
    ok = _try_move_to_needs_user(
        task["id"],
        task_copy,
        "Please clarify requirements. Split the card or reset the Developer visit latch.",
        kind="phase_cycle_cap",
    )
    assert ok is True
    live = find_task_by_id("T-LIVE-MCQ")
    assert live is task
    assert isinstance(live.get("needsUserOptions"), list)
    assert len(live["needsUserOptions"]) >= 3
    assert get_task_lane("T-LIVE-MCQ") == "Needs User"


def test_failed_park_does_not_claim_success():
    from unittest.mock import patch

    initialize()
    from backend import state
    from backend.agents.task_context import find_task_by_id, get_task_lane, init_new_task
    from backend.services.sprint_service import _try_move_to_needs_user
    from backend.services.workflow_settings import save_workflow_settings

    save_workflow_settings({"pauseSprintOnNeedsUser": False, "autonomousMode": False})
    task = init_new_task(
        {
            "id": "T-PARK-FAIL",
            "title": "Big card",
            "description": "Feature work",
            "status": "In Progress",
            "acceptanceCriteria": ["Done"],
        }
    )
    task["phaseCycleCapReached"] = True
    state.SHARED_BOARD = {
        "Backlog": [],
        "In Progress": [task],
        "Needs PO": [],
        "Needs User": [],
        "QA": [],
        "Done": [],
    }
    before_count = int(state.SPRINT_NEEDS_USER_COUNT or 0)
    with patch(
        "backend.services.sprint_service.move_board_stage",
        return_value="Error: admission rejected",
    ):
        ok = _try_move_to_needs_user(
            "T-PARK-FAIL",
            dict(task),
            "Phase cycle cap reached.",
            kind="phase_cycle_cap",
        )
    assert ok is False
    live = find_task_by_id("T-PARK-FAIL")
    assert get_task_lane("T-PARK-FAIL") == "In Progress"
    assert not live.get("needsUserOptions")
    assert int(state.SPRINT_NEEDS_USER_COUNT or 0) == before_count


def test_reconcile_broken_phase_cap_without_options():
    initialize()
    from backend import state
    from backend.agents.task_context import get_task_lane, init_new_task
    from backend.services.needs_user_guard import reconcile_autonomous_needs_user_cards

    task = init_new_task(
        {
            "id": "T-NU-CAP-BROKEN",
            "title": "Import from document",
            "description": "Add import flow",
            "status": "Needs User",
            "acceptanceCriteria": ["User can import a PDF"],
        }
    )
    task["needsUserKind"] = "phase_cycle_cap"
    task["userQuestion"] = "Split the card or reset the Developer visit latch?"
    task["needsUserReason"] = "Developer visit cap reached"
    task["phaseCycleCapReached"] = True
    task.pop("needsUserOptions", None)
    state.SHARED_BOARD = {
        "Backlog": [],
        "In Progress": [],
        "Needs PO": [],
        "Needs User": [task],
        "QA": [],
        "Done": [],
    }
    result = reconcile_autonomous_needs_user_cards()
    assert result["count"] == 1
    assert get_task_lane("T-NU-CAP-BROKEN") == "In Progress"
    assert not task.get("userQuestion")


def test_diagnosis_options_use_recommended_action_not_template():
    task = {
        "id": "T-EXPORT",
        "title": "Implement Export to storage location — core flow",
        "description": "Serialize meals to JSON and save/share from the main menu.",
        "acceptanceCriteria": ["Export from main menu"],
        "lastDiagnosis": {
            "problem": "The repository contains only documentation; there are no Dart/Flutter source files.",
            "recommendedAction": (
                "Verify that the Flutter project scaffold has been initialized. "
                "If not, initialize a new Flutter Android project (`flutter create`)."
            ),
            "suggestedAgent": "user",
        },
    }
    brief = build_needs_user_brief(task, kind="stuck_loop", raw_msg="Need a product decision")
    apply_needs_user_brief(task, brief)
    labels = [str(o.get("label") or "") for o in task["needsUserOptions"]]
    blob = " ".join(labels).lower()
    answers = " ".join(str(o.get("answer") or "") for o in task["needsUserOptions"]).lower()
    assert "simplest option" not in blob
    assert "send back to product owner to refine the spec" not in blob
    assert "answer in my own words" not in blob
    assert "scaffold" in blob or "flutter create" in blob or "flutter create" in answers
    assert task["needsUserOptions"][-1]["id"] == "other"
    assert [o["id"] for o in task["needsUserOptions"]].count("other") == 1


def test_lint_card_without_diagnosis_gets_specific_options():
    task = {
        "id": "T-WIDGET",
        "title": "Fix Undefined Class 'Widget' Error in lib/main.dart",
        "description": "Add missing Flutter import.",
        "acceptanceCriteria": ["flutter analyze clean for main.dart"],
        "lintSourceFile": "lib/main.dart",
        "lastCommandDiagnostics": [
            {
                "file": "lib/main.dart",
                "line": 5,
                "message": "Undefined class 'Widget'",
            }
        ],
        "lastStepOutcome": {
            "exitReason": "tool_failure_stop",
            "suggestedAction": "Add `import 'package:flutter/material.dart';` to lib/main.dart",
        },
    }
    brief = build_needs_user_brief(task, kind="stuck_loop", raw_msg="Stuck on Widget error")
    labels = " ".join(str(o.get("label") or "") for o in brief["options"]).lower()
    assert "widget" in labels or "main.dart" in labels
    assert "continue implementing" not in labels


def test_phase_cycle_cap_with_lint_includes_fix_option():
    task = {
        "id": "T-CAP-LINT",
        "title": "Shopping list generation",
        "description": "Generate a shopping list from planned meals.",
        "acceptanceCriteria": ["List items by aisle"],
        "phaseCycleCapReached": True,
        "lastCommandDiagnostics": [
            {
                "file": "lib/main.dart",
                "line": 13,
                "message": "The method 'Center' isn't defined for the type 'MyApp'",
            }
        ],
    }
    brief = build_needs_user_brief(task, kind="phase_cycle_cap", raw_msg="Phase cycle cap reached.")
    labels = [str(o.get("label") or "").lower() for o in brief["options"]]
    assert any("center" in lab or "lib/main.dart" in lab for lab in labels)
    assert any("split" in lab for lab in labels)
    assert any("latch" in lab or "reset" in lab for lab in labels)
    assert labels[-1] == "other (type below)"
    assert "simplest option" not in " ".join(labels)


def test_no_duplicate_other_option():
    task = {
        "id": "T-OTHER",
        "title": "Theme",
        "description": "Add theme toggle to the settings screen with persistence.",
        "acceptanceCriteria": ["Toggle works"],
    }
    brief = build_needs_user_brief(
        task,
        kind="dev_board_move",
        raw_msg="Needs User: Should the default theme be light mode or dark mode?",
    )
    ids = [o["id"] for o in brief["options"]]
    labels = [str(o.get("label") or "").lower() for o in brief["options"]]
    assert ids.count("other") == 1
    assert labels.count("other (type below)") == 1
    assert not any("answer in my own words" in lab for lab in labels)


def test_llm_mock_replaces_evidence_options():
    from backend.services.needs_user_guard import apply_needs_user_brief, enrich_needs_user_options

    task = {
        "id": "T-LLM",
        "title": "Export to storage",
        "description": "Serialize meals to JSON and save/share.",
        "acceptanceCriteria": ["Export from main menu"],
        "lastDiagnosis": {
            "problem": "No Dart source files",
            "recommendedAction": "Run flutter create then implement export.",
        },
    }
    brief = build_needs_user_brief(task, kind="stuck_loop", raw_msg="How should we proceed?")
    apply_needs_user_brief(task, brief)

    def fake_chat(_prompt: str) -> str:
        return (
            '{"options":['
            '{"id":"a","label":"Scaffold with flutter create then implement export",'
            '"answer":"Run flutter create in the workspace, then implement export.","target":"dev"},'
            '{"id":"b","label":"Point Developer at an existing app directory",'
            '"answer":"The Flutter app lives in a subdirectory; use that.","target":"dev"}'
            "]}"
        )

    assert enrich_needs_user_options(task, chat_fn=fake_chat) is True
    labels = [str(o.get("label") or "").lower() for o in task["needsUserOptions"]]
    assert any("scaffold" in lab and "flutter create" in lab for lab in labels)
    assert task["needsUserOptions"][-1]["id"] == "other"
    assert "simplest option" not in " ".join(labels)


def test_llm_failure_keeps_evidence_options():
    from backend.services.needs_user_guard import apply_needs_user_brief, enrich_needs_user_options

    task = {
        "id": "T-LLM-FAIL",
        "title": "Export to storage",
        "description": "Serialize meals to JSON and save/share.",
        "acceptanceCriteria": ["Export from main menu"],
        "lastDiagnosis": {
            "problem": "No Dart source files",
            "recommendedAction": "Run flutter create then implement export.",
        },
    }
    brief = build_needs_user_brief(task, kind="stuck_loop", raw_msg="How should we proceed?")
    apply_needs_user_brief(task, brief)
    before = list(task["needsUserOptions"])

    def boom(_prompt: str) -> str:
        raise RuntimeError("llm down")

    assert enrich_needs_user_options(task, chat_fn=boom) is False
    assert task["needsUserOptions"] == before
    blob = " ".join(str(o.get("label") or "") for o in before).lower()
    assert "flutter create" in blob or "scaffold" in blob or "no dart" in blob


def test_generic_template_options_are_refreshed():
    from backend.services.needs_user_guard import refresh_generic_needs_user_options

    task = {
        "id": "T-REFRESH",
        "title": "Implement Export to storage location — core flow",
        "description": "Serialize meals to JSON and save/share from the main menu.",
        "acceptanceCriteria": ["Export from main menu"],
        "status": "Needs User",
        "needsUserKind": "stuck",
        "userQuestion": "What decision does Developer need?",
        "needsUserReason": "Agents stopped",
        "needsUserAction": "Answer then Send to Developer",
        "lastDiagnosis": {
            "problem": "No Dart/Flutter source files present",
            "recommendedAction": "Initialize a Flutter project with flutter create, then implement export.",
        },
        "needsUserOptions": [
            {
                "id": "a",
                "label": "Proceed with the simplest option that matches the spec",
                "answer": "Proceed with the simplest implementation.",
                "target": "dev",
            },
            {
                "id": "b",
                "label": "Send back to Product Owner to refine the spec",
                "answer": "Send to PO.",
                "target": "po",
            },
            {"id": "other", "label": "Other (type below)", "answer": "", "target": "dev"},
        ],
    }
    assert refresh_generic_needs_user_options(task) is True
    blob = " ".join(str(o.get("label") or "") for o in task["needsUserOptions"]).lower()
    assert "simplest option" not in blob
    assert "flutter create" in blob or "scaffold" in blob or "initialize" in blob
    assert task["needsUserOptions"][-1]["id"] == "other"


def test_reconcile_refreshes_generic_options_without_moving():
    initialize()
    from backend import state
    from backend.agents.task_context import get_task_lane, init_new_task
    from backend.services.needs_user_guard import reconcile_autonomous_needs_user_cards

    task = init_new_task(
        {
            "id": "T-NU-REFRESH",
            "title": "Export to storage",
            "description": "Serialize meals to JSON and save/share from the main menu.",
            "status": "Needs User",
            "acceptanceCriteria": ["Export from main menu"],
        }
    )
    task["needsUserKind"] = "product_choice"
    task["userQuestion"] = "How should we resolve: no Flutter source files?"
    task["needsUserReason"] = "Developer cannot find Dart files"
    task["needsUserAction"] = "Answer then Send to Developer"
    task["lastDiagnosis"] = {
        "problem": "No Dart/Flutter source files present",
        "recommendedAction": "Initialize a Flutter project with flutter create.",
    }
    task["needsUserOptions"] = [
        {
            "id": "a",
            "label": "Proceed with the simplest option that matches the spec",
            "target": "dev",
        },
        {"id": "b", "label": "Send back to Product Owner to refine the spec", "target": "po"},
        {"id": "other", "label": "Other (type below)", "target": "dev"},
    ]
    state.SHARED_BOARD = {
        "Backlog": [],
        "In Progress": [],
        "Needs PO": [],
        "Needs User": [task],
        "QA": [],
        "Done": [],
    }
    result = reconcile_autonomous_needs_user_cards()
    assert result["count"] == 0
    assert "T-NU-REFRESH" in result.get("refreshed", [])
    assert get_task_lane("T-NU-REFRESH") == "Needs User"
    blob = " ".join(str(o.get("label") or "") for o in task["needsUserOptions"]).lower()
    assert "simplest option" not in blob
    assert "flutter create" in blob or "initialize" in blob

