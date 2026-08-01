"""Needs User guard — import-check questions route to PO/clarification."""

from backend.services.needs_user_guard import is_import_check_shaped, should_escalate_to_needs_user


def test_import_check_not_needs_user():
    task = {"id": "T1", "userResolutions": []}
    allowed, reason = should_escalate_to_needs_user(
        task,
        "Is the flutter package correctly imported in main.dart?",
    )
    assert not allowed
    assert reason == "clarification_use_po"


def test_import_check_pattern():
    assert is_import_check_shaped("Is lodash installed in this project?")
    assert not is_import_check_shaped("What API key should I use for production?")
