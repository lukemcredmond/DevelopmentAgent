"""Regression: get_active_trace must be bound before tool-call recovery in execute_step."""

import inspect

from backend.agents.scrum_agent import ScrumAgent


def test_execute_step_imports_get_active_trace_at_loop_top():
    src = inspect.getsource(ScrumAgent.execute_step)
    loop_import = src.split("set_llm_iterations_max(max_iterations)")[0]
    assert "get_active_trace" in loop_import
    assert (
        src.count("from backend.services.step_diagnostics import get_active_trace")
        == 0
    )
