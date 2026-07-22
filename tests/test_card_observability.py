"""Tests for card work snapshot + live intent observability helpers."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from backend import state
from backend.services.step_diagnostics import (
    build_card_work_snapshot,
    build_live_intent,
    build_step_progress,
    files_written_this_step,
    gates_remaining_for_lane,
)


@pytest.fixture(autouse=True)
def _clean_board():
    state.SHARED_BOARD = {
        "Features": [],
        "Backlog": [],
        "Refinement": [],
        "In Progress": [],
        "Needs PO": [],
        "Needs User": [],
        "Code Review": [],
        "QA": [],
        "Done": [],
    }
    state.LAST_STEP_PROGRESS = None
    state.ACTIVE_STEP_DIAGNOSTICS = None
    yield


def test_gates_remaining_from_in_progress_with_cr():
    with patch(
        "backend.services.workflow_settings.get_workflow_settings",
        return_value={"requireCodeReview": True},
    ):
        assert gates_remaining_for_lane("In Progress") == ["Code Review", "QA", "Done"]
        assert gates_remaining_for_lane("Code Review") == ["QA", "Done"]
        assert gates_remaining_for_lane("QA") == ["Done"]
        assert gates_remaining_for_lane("Done") == []


def test_gates_remaining_without_cr():
    with patch(
        "backend.services.workflow_settings.get_workflow_settings",
        return_value={"requireCodeReview": False},
    ):
        assert gates_remaining_for_lane("In Progress") == ["QA", "Done"]


def test_build_card_work_snapshot_subtasks_and_acs():
    parent = {
        "id": "PARENT-1",
        "title": "Parent",
        "description": "d",
        "status": "In Progress",
        "acceptanceCriteria": ["a", "b", "c"],
        "subtaskIds": ["CHILD-1", "CHILD-2"],
        "stuckLoops": 2,
        "poRoundTrips": 1,
    }
    child_done = {
        "id": "CHILD-1",
        "title": "Done child",
        "description": "d",
        "status": "Done",
        "subtaskIds": [],
    }
    child_open = {
        "id": "CHILD-2",
        "title": "Open child",
        "description": "d",
        "status": "Backlog",
        "subtaskIds": [],
    }
    state.SHARED_BOARD["In Progress"] = [parent]
    state.SHARED_BOARD["Done"] = [child_done]
    state.SHARED_BOARD["Backlog"] = [child_open]

    with patch(
        "backend.services.workflow_settings.get_workflow_settings",
        return_value={"requireCodeReview": True},
    ):
        snap = build_card_work_snapshot(parent)

    assert snap["subtasksDone"] == 1
    assert snap["subtasksTotal"] == 2
    assert snap["acCount"] == 3
    assert snap["stuckLoops"] == 2
    assert snap["stepsOnCard"] == 2
    assert snap["poRoundTrips"] == 1
    assert snap["gatesRemaining"] == ["Code Review", "QA", "Done"]


def test_build_live_intent_phases():
    assert "Awaiting Ollama" in build_live_intent(phase="awaiting_ollama", iteration=2, max_iterations=8)
    assert "Thinking" in build_live_intent(phase="thinking", iteration=1, max_iterations=8)
    assert "apply_patch" in build_live_intent(phase="plan_reject", reject_label="plan-only", iteration=3, max_iterations=8)
    assert "read_file" in build_live_intent(phase="tool", tool_name="read_file", tool_summary="src/a.py")


def test_files_written_this_step_from_log():
    paths = files_written_this_step(
        [
            {"toolName": "read_file", "success": True, "summary": "a.py"},
            {"toolName": "write_file", "success": True, "summary": "src/app.py (120 chars)"},
            {"toolName": "apply_patch", "success": True, "summary": "src/app.py (replace 10 chars)"},
            {"toolName": "write_file", "success": False, "summary": "bad.py (0 chars)"},
        ]
    )
    assert paths == ["src/app.py"]


def test_build_step_progress_includes_intent_and_card():
    task = {
        "id": "T-OBS",
        "title": "Obs",
        "description": "d",
        "status": "In Progress",
        "acceptanceCriteria": ["one"],
        "subtaskIds": [],
        "stuckLoops": 0,
    }
    state.SHARED_BOARD["In Progress"] = [task]
    with patch(
        "backend.services.workflow_settings.get_workflow_settings",
        return_value={"requireCodeReview": False},
    ):
        progress = build_step_progress(
            task_id="T-OBS",
            iterations_used=3,
            iterations_max=8,
            intent="Thinking (iter 3/8)",
            why_card_stayed="read-only step",
            suggested_action="Run again with edits",
        )
    assert progress["intent"] == "Thinking (iter 3/8)"
    assert progress["whyCardStayed"] == "read-only step"
    assert progress["suggestedAction"] == "Run again with edits"
    assert progress["cardProgress"]["acCount"] == 1
    assert progress["cardProgress"]["gatesRemaining"] == ["QA", "Done"]


def test_publish_sprint_progress_includes_intent():
    from backend.services.sprint_service import publish_sprint_progress

    events = []

    def capture(event_type, payload):
        events.append((event_type, payload))

    with patch("backend.services.sprint_service.publish_event", side_effect=capture):
        publish_sprint_progress(
            phase="sprint_step",
            step=2,
            max_steps=20,
            agent="Developer",
            task_id="T1",
            task_title="Title",
            lane="In Progress",
            status="LLM iter 2/8",
            intent="Thinking (iter 2/8)",
            card_progress={"subtasksDone": 0, "subtasksTotal": 2, "acCount": 1},
        )
    assert len(events) == 1
    assert events[0][0] == "sprint_progress"
    assert events[0][1]["intent"] == "Thinking (iter 2/8)"
    assert events[0][1]["cardProgress"]["subtasksTotal"] == 2
