"""Offline simulation confirmation gate."""

from __future__ import annotations

from backend.agents.task_context import find_task_by_id, init_new_task
from backend.bootstrap import initialize
from backend import state
from backend.services.simulation_gate import (
    apply_dev_offline_if_file_exists,
    apply_simulation_confirmation,
    build_proposal,
    dev_simulation_target,
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


def _dev_task_without_workspace_file():
    """Title heuristic -> meal_service.js (usually absent in test workspace)."""
    task = init_new_task(
        {
            "id": "T-NEW",
            "title": "meal planner api",
            "description": "d",
            "status": "In Progress",
        }
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
    task = _dev_task_without_workspace_file()
    preview = preview_sprint_dev(task)
    prop = build_proposal(
        kind="sprint_dev",
        task_id="T-NEW",
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
    assert content and "module.exports" in content


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
    task = _dev_task_without_workspace_file()
    preview = preview_sprint_dev(task)
    prop = build_proposal(
        kind="sprint_dev",
        task_id="T-NEW",
        agent="Developer",
        title=task["title"],
        summary="Write offline dev file",
        default_preview=preview,
        source="sprint_dev",
    )
    assert propose_simulation(prop) == "applied"
    assert get_pending_simulation_public() is None
    content = read_workspace_file(preview["fileName"])
    assert content and "module.exports" in content


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


def test_confirm_with_existing_file_does_not_overwrite_stub():
    initialize()
    from backend.workspace.files import write_workspace_file
    from backend.agents.task_context import get_task_lane

    state.PENDING_SIMULATION = None
    save_workflow_settings({"confirmSimulationFallback": True})
    task = _dev_task()
    original = "// user-authored existing file\nmodule.exports = {};\n"
    write_workspace_file("index.js", original)
    preview = preview_sprint_dev(task)
    assert preview.get("workspaceFileExists") is True
    prop = build_proposal(
        kind="sprint_dev",
        task_id="T-SIM",
        agent="Developer",
        title=task["title"],
        summary="dev",
        default_preview=preview,
        source="sprint_dev",
    )
    try_defer_simulation(prop)
    result = apply_simulation_confirmation(accept=True)
    assert result["ok"] is True
    assert read_workspace_file("index.js") == original
    assert get_task_lane("T-SIM") != "In Progress"


def test_use_workspace_file_override_advances_without_overwrite():
    initialize()
    from backend.workspace.files import write_workspace_file
    from backend.agents.task_context import get_task_lane

    state.PENDING_SIMULATION = None
    save_workflow_settings({"confirmSimulationFallback": True})
    task = _dev_task()
    original = "existing content only\n"
    write_workspace_file("index.js", original)
    preview = preview_sprint_dev(task)
    prop = build_proposal(
        kind="sprint_dev",
        task_id="T-SIM",
        agent="Developer",
        title=task["title"],
        summary="dev",
        default_preview=preview,
        source="sprint_dev",
    )
    try_defer_simulation(prop)
    result = apply_simulation_confirmation(accept=False, override_target="use_workspace_file")
    assert result["ok"] is True
    assert read_workspace_file("index.js") == original
    assert get_task_lane("T-SIM") != "In Progress"


def test_apply_dev_offline_if_file_exists_skips_pending():
    initialize()
    from backend.workspace.files import write_workspace_file
    from backend.agents.task_context import get_task_lane

    state.PENDING_SIMULATION = None
    save_workflow_settings({"simulationAutoUseExistingFile": True, "confirmSimulationFallback": True})
    task = _dev_task()
    write_workspace_file("index.js", "auto apply content\n")
    assert apply_dev_offline_if_file_exists(task, task_id="T-SIM", lane_before="In Progress") is True
    assert get_pending_simulation_public() is None
    assert read_workspace_file("index.js") == "auto apply content\n"
    assert get_task_lane("T-SIM") != "In Progress"


def test_write_file_transcript_path_resolves_existing_file():
    initialize()
    from backend.workspace.files import write_workspace_file

    save_workflow_settings({"simulationAutoUseExistingFile": True})
    task = _dev_task()
    task["transcript"] = [
        {
            "toolName": "write_file",
            "toolSuccess": True,
            "toolArgs": {"path": "src/feature.py"},
        }
    ]
    write_workspace_file("src/feature.py", "print('ok')\n")
    path, _, existing = dev_simulation_target(task)
    assert path == "src/feature.py"
    assert existing is not None
    assert "print" in existing


def test_apply_dev_offline_no_file_still_defers(monkeypatch):
    initialize()

    state.PENDING_SIMULATION = None
    save_workflow_settings({"simulationAutoUseExistingFile": True, "confirmSimulationFallback": True})
    task = init_new_task(
        {
            "id": "T-NOF",
            "title": "meal planner api",
            "description": "d",
            "status": "In Progress",
        }
    )
    state.SHARED_BOARD["In Progress"] = [task]

    monkeypatch.setattr(
        "backend.services.simulation_gate.dev_simulation_target",
        lambda _t: ("meal_service.js", "stub", None),
    )
    assert apply_dev_offline_if_file_exists(task, task_id="T-NOF", lane_before="In Progress") is False
    preview = preview_sprint_dev(task)
    prop = build_proposal(
        kind="sprint_dev",
        task_id="T-NOF",
        agent="Developer",
        title=task["title"],
        summary="dev",
        default_preview=preview,
        source="sprint_dev",
    )
    assert try_defer_simulation(prop) is True
    assert get_pending_simulation_public() is not None


def test_auto_sprint_stops_when_simulation_pending(monkeypatch):
    initialize()
    from backend.services.sprint_service import run_auto_sprint, run_sprint_step as real_step

    state.PENDING_SIMULATION = None
    save_workflow_settings({"confirmSimulationFallback": True})
    _dev_task()
    step_calls = {"n": 0}

    def fake_step(brief: str, ollama_url: str) -> None:
        step_calls["n"] += 1
        if step_calls["n"] == 1:
            task = find_task_by_id("T-SIM")
            preview = preview_sprint_dev(task)
            prop = build_proposal(
                kind="sprint_dev",
                task_id="T-SIM",
                agent="Developer",
                title="Build index page",
                summary="dev",
                default_preview=preview,
                source="sprint_dev",
            )
            try_defer_simulation(prop)

    monkeypatch.setattr(
        "backend.services.sprint_service.run_sprint_step",
        fake_step,
    )
    monkeypatch.setattr(
        "backend.services.sprint_service.has_sprint_work",
        lambda: step_calls["n"] < 1 or not get_pending_simulation_public(),
    )

    summary = run_auto_sprint("brief", "http://localhost:11434", max_steps=5)
    assert summary.get("status") == "simulation_pending"
    assert step_calls["n"] == 1


def get_task_lane(task: dict) -> str:
    from backend.agents.task_context import get_task_lane as _lane

    return _lane(task["id"]) or task.get("status") or ""
