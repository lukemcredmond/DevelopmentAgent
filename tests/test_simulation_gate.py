"""Offline simulation confirmation gate."""

from __future__ import annotations

from backend.agents.task_context import find_task_by_id, init_new_task
from backend.bootstrap import initialize
from backend import state
from backend.services.simulation_gate import (
    apply_simulation_confirmation,
    build_proposal,
    get_pending_simulation_public,
    preview_sprint_dev,
    propose_simulation,
    try_defer_simulation,
)
from backend.services.workflow_settings import save_workflow_settings
from backend.workspace.files import read_workspace_file


def _dev_task():
    task = init_new_task(
        {"id": "T-SIM", "title": "Build index page", "description": "d", "status": "In Progress"}
    )
    state.SHARED_BOARD["In Progress"] = [task]
    return task


def test_defer_dev_simulation_when_confirm_on():
    initialize()
    state.PENDING_SIMULATION = None
    save_workflow_settings({"confirmSimulationFallback": True})
    task = _dev_task()
    preview = preview_sprint_dev(task)
    prop = build_proposal(
        kind="sprint_dev",
        task_id="T-SIM",
        agent="Developer",
        title=task["title"],
        summary="Write offline dev file",
        default_preview=preview,
        source="sprint_dev",
    )
    assert try_defer_simulation(prop) is True
    assert get_pending_simulation_public() is not None
    assert get_pending_simulation_public()["kind"] == "sprint_dev"


def test_confirm_default_applies_dev_file():
    initialize()
    state.PENDING_SIMULATION = None
    save_workflow_settings({"confirmSimulationFallback": True})
    task = _dev_task()
    preview = preview_sprint_dev(task)
    prop = build_proposal(
        kind="sprint_dev",
        task_id="T-SIM",
        agent="Developer",
        title=task["title"],
        summary="Write offline dev file",
        default_preview=preview,
        source="sprint_dev",
    )
    try_defer_simulation(prop)
    result = apply_simulation_confirmation(accept=True)
    assert result["ok"] is True
    assert get_pending_simulation_public() is None
    content = read_workspace_file(preview["fileName"])
    assert content and "init" in content


def test_decline_agent_text_does_not_move_lane():
    initialize()
    state.PENDING_SIMULATION = None
    save_workflow_settings({"confirmSimulationFallback": True})
    task = _dev_task()
    preview = preview_sprint_dev(task)
    prop = build_proposal(
        kind="sprint_dev",
        task_id="T-SIM",
        agent="Developer",
        title=task["title"],
        summary="Write offline dev file",
        default_preview=preview,
        source="sprint_dev",
    )
    try_defer_simulation(prop)
    result = apply_simulation_confirmation(
        accept=False,
        override_target="agent_text",
        override_value="Custom offline note",
    )
    assert result["ok"] is True
    t = find_task_by_id("T-SIM")
    assert t is not None
    assert get_task_lane(t) == "In Progress"


def test_confirm_off_applies_immediately():
    initialize()
    state.PENDING_SIMULATION = None
    save_workflow_settings({"confirmSimulationFallback": False})
    task = _dev_task()
    preview = preview_sprint_dev(task)
    prop = build_proposal(
        kind="sprint_dev",
        task_id="T-SIM",
        agent="Developer",
        title=task["title"],
        summary="Write offline dev file",
        default_preview=preview,
        source="sprint_dev",
    )
    assert propose_simulation(prop) == "applied"
    assert get_pending_simulation_public() is None
    content = read_workspace_file(preview["fileName"])
    assert content and "init" in content


def test_second_propose_replaces_pending():
    initialize()
    state.PENDING_SIMULATION = None
    save_workflow_settings({"confirmSimulationFallback": True})
    task = _dev_task()
    p1 = build_proposal(
        kind="sprint_dev",
        task_id="T-SIM",
        agent="Developer",
        title=task["title"],
        summary="First",
        default_preview=preview_sprint_dev(task),
        source="sprint_dev",
    )
    p2 = build_proposal(
        kind="sprint_cr",
        task_id="T-SIM",
        agent="Code Reviewer",
        title=task["title"],
        summary="Second",
        default_preview={"likelyOutcome": "QA"},
        source="sprint_cr",
    )
    try_defer_simulation(p1)
    try_defer_simulation(p2)
    pending = get_pending_simulation_public()
    assert pending is not None
    assert pending["kind"] == "sprint_cr"


def get_task_lane(task: dict) -> str:
    from backend.agents.task_context import get_task_lane as _lane

    return _lane(task["id"]) or task.get("status") or ""
