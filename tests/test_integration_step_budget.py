"""Integration cards get higher dev step wall."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from backend.services.composer_dev_step import run_composer_dev_step


def test_integration_multiplier_extends_wall():
    agent = MagicMock()
    agent.execute_step.return_value = "ok"
    agent.role = "Developer"
    task = {"id": "T1", "integrationEditPaths": ["lib/main.dart", "pubspec.yaml"]}
    with patch(
        "backend.services.composer_dev_step.get_workflow_settings",
        return_value={
            "implementerMaxDevStepWallSec": 100,
            "integrationStepBudgetMultiplier": 2.0,
        },
    ):
        with patch(
            "backend.services.fix_verify_loop._agent_had_successful_write",
            return_value=False,
        ):
            run_composer_dev_step(agent, task, "prompt", max_iterations=5)
    kwargs = agent.execute_step.call_args.kwargs
    assert kwargs.get("max_step_duration_sec") == 200
    assert kwargs.get("max_iterations") == 10
