"""Slim dev prompt for forcePatchNextDevStep on first attempt."""

from backend.services.sprint_service import _should_slim_dev_prompt


def test_slim_when_force_patch_flag_set_on_card():
    task = {
        "title": "Lint: main.dart",
        "consecutiveBadExits": 0,
        "forcePatchNextDevStep": True,
    }
    assert _should_slim_dev_prompt(task) is True
