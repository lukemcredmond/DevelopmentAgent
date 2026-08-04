"""Hints after read_file on manifest files (verify vs edit)."""

from backend.agents.scrum_agent import (
    _read_file_followup_system_message,
    _task_suggests_dependency_verify_only,
)
from backend.agents.task_context import init_new_task


def test_verify_only_pubspec_task_gets_read_hint_not_apply_patch():
    task = init_new_task(
        {
            "id": "T-V",
            "title": "Verify firebase_auth",
            "description": "Check pubspec",
            "acceptanceCriteria": ["firebase_auth:^4.16.0 is declared in pubspec.yaml"],
        }
    )
    assert _task_suggests_dependency_verify_only(task)
    msg = _read_file_followup_system_message("pubspec.yaml", task=task)
    assert "apply_patch unless" in msg
    assert "Do not apply_patch unless" in msg or "unless the AC requires" in msg
    assert "call apply_patch now to add" not in msg


def test_add_dependency_task_still_pushes_patch():
    task = init_new_task(
        {
            "id": "T-A",
            "title": "Add firebase",
            "acceptanceCriteria": ["Add firebase_auth ^4.16.0 to pubspec.yaml"],
        }
    )
    assert not _task_suggests_dependency_verify_only(task)
    msg = _read_file_followup_system_message("pubspec.yaml", task=task)
    assert "apply_patch now" in msg
