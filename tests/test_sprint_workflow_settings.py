"""Sprint-critical workflow settings: persist and runtime consumers."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from backend import state
from backend.agents.task_context import init_new_task, normalize_task
from backend.bootstrap import initialize
from backend.services.fix_verify_loop import run_fix_verify_loop
from backend.services.workflow_settings import reset_workflow_settings, save_workflow_settings


def test_max_llm_iterations_persisted_for_sprint():
    initialize()
    reset_workflow_settings()
    save_workflow_settings({"maxLlmIterationsPerStep": 12})
    from backend.services.sprint_service import _llm_iterations

    assert _llm_iterations() == 12


def test_fix_verify_round_two_uses_full_max_iterations():
    initialize()
    reset_workflow_settings()
    save_workflow_settings(
        {
            "enableFixVerifyLoop": True,
            "requireCleanLint": True,
            "maxFixVerifyRounds": 3,
        }
    )
    task = init_new_task({"id": "T-FV-IT", "title": "Iter cap", "description": "d"})
    normalize_task(task)
    state.SPRINT_CANCEL = False

    max_iters_seen: list[int] = []
    lint_calls = {"n": 0}

    def execute_step(prompt, max_iterations=4):
        max_iters_seen.append(int(max_iterations))
        return "work"

    agent = MagicMock()
    agent.execute_step.side_effect = execute_step

    lint_dirty = MagicMock()
    lint_dirty.outcome = "fail"
    lint_dirty.diagnostics = [{"severity": "error", "message": "x", "file": "a.py", "line": 1}]
    lint_dirty.summary = "1 error"

    lint_ok = MagicMock()
    lint_ok.outcome = "ok"
    lint_ok.diagnostics = []
    lint_ok.summary = "clean"

    def run_lint(_cmd):
        lint_calls["n"] += 1
        return lint_dirty if lint_calls["n"] == 1 else lint_ok

    with patch(
        "backend.services.fix_verify_loop.derive_project_lint_command",
        return_value="echo lint",
    ), patch(
        "backend.services.fix_verify_loop.run_workspace_command",
        side_effect=run_lint,
    ), patch(
        "backend.services.fix_verify_loop.maybe_fanout_lint_diagnostics",
        return_value={"kept": lint_dirty.diagnostics, "spawned": []},
    ), patch(
        "backend.services.fix_verify_loop.find_task_by_id",
        return_value=task,
    ):
        run_fix_verify_loop(agent, task, "prompt", max_iterations=12)

    assert len(max_iters_seen) >= 2
    assert max_iters_seen[0] == 12
    assert max_iters_seen[1] == 12


def test_fix_verify_exposes_round_on_agent_run_sse():
    from backend.agents.agent_run import start_run, update_run

    initialize()
    state.FIX_VERIFY_ROUND = 2
    state.FIX_VERIFY_MAX_ROUNDS = 3
    published: list[dict] = []

    with patch("backend.agents.agent_run.publish_event", side_effect=lambda _t, d: published.append(d)):
        start_run("T-1", "Developer", max_iterations=12)
        update_run(iteration=3)

    assert published
    last = published[-1]
    assert last.get("fixVerifyRound") == 2
    assert last.get("fixVerifyMaxRounds") == 3
    state.FIX_VERIFY_ROUND = None
    state.FIX_VERIFY_MAX_ROUNDS = None


def test_max_sprint_steps_limits_auto_sprint():
    initialize()
    reset_workflow_settings()
    save_workflow_settings({"maxSprintSteps": 2, "autoSprintSessionRefreshEnabled": False})
    state.SPRINT_CANCEL = False

    from backend.services import sprint_service

    step_calls = {"n": 0}

    def fake_step(_brief, _url):
        step_calls["n"] += 1

    with patch.object(sprint_service, "run_sprint_step", side_effect=fake_step):
        with patch.object(sprint_service, "has_sprint_work", return_value=True):
            with patch(
                "backend.services.simulation_gate.has_pending_simulation", return_value=False
            ):
                summary = sprint_service.run_auto_sprint("brief", "http://localhost:11434")
    assert step_calls["n"] == 2
    assert summary.get("status") in ("max_steps", "completed")


def test_auto_sprint_session_refresh_minutes_from_settings():
    initialize()
    reset_workflow_settings()
    save_workflow_settings(
        {
            "autoSprintSessionRefreshEnabled": True,
            "autoSprintSessionRefreshMinutes": 30,
        }
    )
    task = init_new_task({"id": "RUN-REF", "title": "Work", "description": "d", "status": "Backlog"})
    state.SHARED_BOARD["Backlog"] = [task]
    state.SPRINT_CANCEL = False

    from backend.services import sprint_service

    with patch.object(sprint_service, "run_sprint_step", return_value=None):
        with patch.object(sprint_service, "has_sprint_work", return_value=True):
            with patch(
                "backend.services.simulation_gate.has_pending_simulation", return_value=False
            ):
                # 31 minutes elapsed → should refresh (min 60 sec floor uses 30*60=1800)
                with patch("time.monotonic", side_effect=[0.0, 1801.0]):
                    summary = sprint_service.run_auto_sprint(
                        "brief", "http://localhost:11434", max_steps=10
                    )
    assert summary.get("status") == "session_refresh"


def test_require_clean_lint_enables_fix_verify_path():
    initialize()
    reset_workflow_settings()
    save_workflow_settings({"enableFixVerifyLoop": False, "requireCleanLint": True})

    agent = MagicMock()
    agent.execute_step.return_value = "once"
    task = {"id": "T-RC", "title": "x"}

    with patch(
        "backend.services.fix_verify_loop.derive_project_lint_command",
        return_value=None,
    ):
        out = run_fix_verify_loop(agent, task, "p", max_iterations=8)
    assert out == "once"
    agent.execute_step.assert_called_once_with("p", max_iterations=8)
