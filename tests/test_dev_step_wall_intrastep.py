"""Implementer dev step wall enforced inside execute_step."""

from unittest.mock import MagicMock, patch

from backend import state
from backend.bootstrap import initialize
from backend.services.step_diagnostics import derive_exit_reason


def test_derive_exit_reason_dev_step_wall_flag():
    initialize()
    state.DEV_STEP_INTRASTEP_WALL = True
    reason = derive_exit_reason(
        agent_result="Timed out: Agent step exceeded 3 minute(s)",
        tools_used=set(),
        lane_before="In Progress",
        lane_after="In Progress",
    )
    assert reason == "dev_step_wall"
    state.DEV_STEP_INTRASTEP_WALL = False


def test_composer_dev_step_passes_wall_to_execute_step():
    initialize()
    from backend.services.composer_dev_step import run_composer_dev_step

    agent = MagicMock()
    agent.execute_step.return_value = "done"
    task = {"id": "T-WALL", "title": "t"}

    with patch("backend.services.composer_dev_step.get_workflow_settings") as mock_ws:
        mock_ws.return_value = {"implementerMaxDevStepWallSec": 120}
        run_composer_dev_step(agent, task, "prompt", max_iterations=3, task_id="T-WALL")

    agent.execute_step.assert_called_once()
    _args, kwargs = agent.execute_step.call_args
    assert kwargs.get("max_step_duration_sec") == 120
