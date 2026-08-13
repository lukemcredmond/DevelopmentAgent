"""Tests for Dev focus slice advancement and caps."""

from backend.services.focus_slice import (
    advance_focus,
    all_focus_slices_done,
    default_focus_mode,
    ensure_focus_initialized,
    focus_cap_reached,
    micro_step_complete,
    should_block_lane_advance_for_focus,
)


def test_default_focus_mode_ac_when_multiple_ac():
    task = {
        "id": "X",
        "acceptanceCriteria": ["one", "two"],
        "subtaskIds": [],
    }
    assert default_focus_mode(task) == "ac"


def test_advance_focus_moves_ac_index():
    task = {
        "id": "X",
        "focusMode": "ac",
        "focusAcIndex": 0,
        "acceptanceCriteria": ["a", "b"],
        "focusStepsRun": 0,
        "files": [],
        "decisions": [],
        "transcript": [],
        "blockedBy": [],
        "workType": "implementation",
        "requiresDev": True,
    }
    ensure_focus_initialized(task)
    assert advance_focus(task) is True
    assert task["focusAcIndex"] == 1


def test_focus_cap_blocks_infinite_micro_steps():
    task = {
        "id": "X",
        "focusMode": "ac",
        "focusAcIndex": 0,
        "acceptanceCriteria": ["a", "b", "c"],
        "focusStepsRun": 8,
        "files": [],
        "decisions": [],
        "transcript": [],
        "blockedBy": [],
        "workType": "implementation",
        "requiresDev": True,
    }
    ensure_focus_initialized(task)
    assert focus_cap_reached(task) is True
    assert should_block_lane_advance_for_focus(task) is True


def test_micro_step_complete_on_focus_done_decision():
    task = {
        "id": "X",
        "decisions": [{"type": "focus_done", "summary": "AC1 done"}],
        "focusMode": "ac",
        "focusAcIndex": 0,
        "acceptanceCriteria": ["a", "b"],
    }
    assert micro_step_complete(task, "done") is True


def test_all_focus_done_when_last_ac_and_checklist():
    task = {
        "id": "X",
        "focusMode": "ac",
        "focusAcIndex": 1,
        "acceptanceCriteria": ["a", "b"],
        "acChecklist": [True, True],
    }
    assert all_focus_slices_done(task) is True
