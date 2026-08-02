"""Regression: _publish_work_progress must not shadow find_task_by_id."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from backend import state
from backend.agents.scrum_agent import ScrumAgent


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
    state.SPRINT_PROGRESS_MAX = 0
    state.SPRINT_PROGRESS_STEP = 0
    state.ACTIVE_AGENT_RUN = None
    yield


def test_publish_work_progress_uses_module_find_task_by_id_before_sprint_emit():
    task = {
        "id": "FOCUS-1",
        "title": "Focus card",
        "description": "d",
        "status": "In Progress",
        "acceptanceCriteria": ["ac1", "ac2"],
        "focusMode": "ac",
        "focusAcIndex": 1,
        "files": [],
        "decisions": [],
        "transcript": [],
        "blockedBy": [],
    }
    state.SHARED_BOARD["In Progress"] = [task]
    state.SPRINT_PROGRESS_MAX = 5
    state.SPRINT_PROGRESS_STEP = 2

    agent = ScrumAgent(role="Developer", model="test", system_prompt="sys")
    captured: dict = {}

    def _capture_update(**kwargs):
        captured.update(kwargs)

    with patch("backend.agents.scrum_agent.update_run", side_effect=_capture_update):
        with patch("backend.services.sprint_service.publish_sprint_progress") as pub:
            agent._publish_work_progress(
                task_id="FOCUS-1",
                intent="LLM iter 1/8",
                status="LLM iter 1/8",
                iteration=1,
                max_iterations=8,
                run_status="thinking",
                prompt_section="bundle_0",
            )
            pub.assert_called_once()
            call_kw = pub.call_args.kwargs
            assert call_kw["focus_ac_index"] == 1
            assert call_kw["prompt_section"] == "bundle_0"

    assert captured.get("focus_ac_index") == 1
    assert captured.get("prompt_section") == "bundle_0"
