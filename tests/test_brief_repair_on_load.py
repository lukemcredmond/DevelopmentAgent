"""Brief repair from original_brief on project load."""

from __future__ import annotations

from backend import state
from backend.bootstrap import initialize
from backend.services.brief_service import repair_brief_from_original_on_load


def test_repair_placeholder_brief_from_original():
    initialize()
    state.PROJECT_BRIEF = "brief"
    state.PROJECT_ORIGINAL_BRIEF = "Flutter meal planner with offline SQLite."
    assert repair_brief_from_original_on_load()
    assert "Flutter" in state.PROJECT_BRIEF
