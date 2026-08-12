"""Lane advance blocked on unhealthy Dev exit reasons."""

from __future__ import annotations

from backend.bootstrap import initialize
from backend.agents.task_context import init_new_task, normalize_task
from backend.services.sprint_service import (
    _dev_unhealthy_exit_blocks_advance,
    _provisional_dev_exit_reason,
)
from backend.services.workflow_settings import reset_workflow_settings, save_workflow_settings


def test_unhealthy_exit_blocks_advance_helper():
    initialize()
    reset_workflow_settings()
    save_workflow_settings({"forceCompleteOnUnhealthyExit": False})
    assert _dev_unhealthy_exit_blocks_advance("ollama_fallback") is True
    assert _dev_unhealthy_exit_blocks_advance("max_iterations") is True
    assert _dev_unhealthy_exit_blocks_advance("completed_with_writes") is False


def test_force_complete_setting_allows_unhealthy_advance():
    initialize()
    reset_workflow_settings()
    save_workflow_settings({"forceCompleteOnUnhealthyExit": True})
    assert _dev_unhealthy_exit_blocks_advance("ollama_fallback") is False


def test_provisional_exit_reason_maps_simulation_fallback():
    initialize()
    reset_workflow_settings()
    reason = _provisional_dev_exit_reason("SIMULATION_FALLBACK", lane_before="In Progress")
    assert reason == "ollama_fallback"


def test_provisional_exit_reason_maps_max_iterations():
    initialize()
    reset_workflow_settings()
    reason = _provisional_dev_exit_reason(
        "Max tool iterations (6) reached",
        lane_before="In Progress",
    )
    assert reason == "max_iterations"
