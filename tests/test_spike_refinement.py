"""Spike card workflow during backlog refinement."""

import json
from unittest.mock import patch

from backend.bootstrap import initialize


def _board_with_refinement_task():
    from backend import state
    from backend.agents.task_context import init_new_task, init_refinement_fields

    parent = init_new_task(
        {
            "id": "TASK-PARENT",
            "title": "Add auth middleware",
            "description": "Protect API routes",
            "acceptanceCriteria": ["JWT validated on protected routes"],
            "status": "Refinement",
        }
    )
    init_refinement_fields(parent)
    state.SHARED_BOARD = {
        "Refinement": [parent],
        "Backlog": [],
        "In Progress": [],
        "Needs PO": [],
        "Needs User": [],
        "Code Review": [],
        "QA": [],
        "Done": [],
    }
    return parent


@patch("backend.services.sprint_service.agent_dev")
def test_refinement_dev_needs_spike_creates_spike_card(mock_dev, monkeypatch):
    initialize()
    from backend import state
    from backend.agents.task_context import find_task_by_id
    from backend.services.sprint_service import _apply_refinement_dev_result

    monkeypatch.setattr(
        "backend.agents.task_context.get_workflow_settings",
        lambda: {"requireBacklogRefinement": True, "maxRefinementRoundTrips": 3},
    )
    parent = _board_with_refinement_task()
    result = json.dumps(
        {
            "ready": False,
            "needsSpike": True,
            "spikeObjective": "Verify existing JWT middleware in codebase",
            "explorationNotes": "Unknown auth stack",
        }
    )
    task = find_task_by_id("TASK-PARENT")
    assert task is not None
    assert _apply_refinement_dev_result(task, result) is True

    spikes = [t for t in state.SHARED_BOARD.get("Refinement", []) if t.get("workType") == "spike"]
    assert len(spikes) == 1
    assert spikes[0].get("spikeForTaskId") == "TASK-PARENT"
    assert parent.get("refinementStatus") == "spike_pending"
    assert parent.get("needsSpike") is True


@patch("backend.services.sprint_service.agent_dev")
def test_spike_completion_merges_into_parent(mock_dev, monkeypatch):
    initialize()
    from backend import state
    from backend.agents.task_context import create_spike_task, find_task_by_id
    from backend.services.sprint_service import _apply_spike_result

    monkeypatch.setattr(
        "backend.agents.task_context.get_workflow_settings",
        lambda: {"requireBacklogRefinement": True, "maxRefinementRoundTrips": 3},
    )
    parent = _board_with_refinement_task()
    spike = create_spike_task(parent, "Find JWT usage")
    result = json.dumps(
        {
            "findings": "Uses ASP.NET JWT bearer middleware.",
            "recommendations": "Extend existing AddAuthentication setup.",
            "openQuestions": ["Which routes stay public?"],
        }
    )
    assert _apply_spike_result(spike, result) is True

    updated_parent = find_task_by_id("TASK-PARENT")
    assert updated_parent is not None
    assert "JWT bearer" in (updated_parent.get("refinementNotes") or "")
    assert updated_parent.get("needsSpike") is False
    assert updated_parent.get("refinementStatus") == "pending"
    assert spike.get("spikeStatus") == "complete"


def test_next_spike_task_before_refinement(monkeypatch):
    initialize()
    from backend import state
    from backend.agents.task_context import create_spike_task, next_refinement_task, next_spike_task

    monkeypatch.setattr(
        "backend.agents.task_context.get_workflow_settings",
        lambda: {"requireBacklogRefinement": True, "maxRefinementRoundTrips": 3},
    )
    parent = _board_with_refinement_task()
    parent["refinementStatus"] = "spike_pending"
    create_spike_task(parent, "Explore auth")

    assert next_spike_task() is not None
    assert next_refinement_task() is None
