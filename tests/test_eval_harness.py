"""Unit tests for the offline eval harness (no live LLM required)."""

import json
from pathlib import Path

import pytest

from backend import state
from backend.services.eval_harness import (
    EvalTask,
    detect_changed_files,
    economics_from_diagnostics,
    format_report,
    load_tasks,
    run_eval_task,
    seed_workspace,
    summarize,
)

SIMPLE_TASK = EvalTask(
    id="demo",
    title="Add greet()",
    description="Add a greet function.",
    seed_files={"app.py": "# empty\n", "test_app.py": "from app import greet\n\n\ndef test():\n    assert greet() == 'hi'\n"},
    verify_command="python -m pytest -q",
    expect_changed_files=["app.py"],
    protected_files=["test_app.py"],
    max_steps=2,
)


def test_load_tasks_reads_shipped_golden_tasks():
    # The shipped suite must stay loadable; a malformed task file should fail loudly.
    tasks = load_tasks()
    assert len(tasks) >= 8
    ids = {t.id for t in tasks}
    assert "01-add-function" in ids
    for task in tasks:
        assert task.verify_command, f"{task.id} has no verify command"
        assert task.protected_files, f"{task.id} does not protect its test file"


def test_load_tasks_only_filter_rejects_unknown_id():
    with pytest.raises(ValueError, match="Unknown eval task id"):
        load_tasks(only=["does-not-exist"])


def test_from_dict_requires_id_and_title():
    with pytest.raises(ValueError, match="missing required field"):
        EvalTask.from_dict({"title": "no id"})


def test_seed_and_detect_changed_files(tmp_path: Path):
    stamps = seed_workspace(SIMPLE_TASK, tmp_path)
    assert (tmp_path / "app.py").exists()
    assert detect_changed_files(tmp_path, stamps) == []

    # A brand new file and a modified file both count as changes.
    (tmp_path / "new.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "app.py").write_text("def greet():\n    return 'hi'\n", encoding="utf-8")
    changed = detect_changed_files(tmp_path, stamps)
    assert set(changed) == {"new.py", "app.py"}


def test_run_eval_task_passes_when_agent_makes_the_change(tmp_path: Path):
    """Happy path: a runner that writes the right code scores as a pass."""

    def runner(_brief: str) -> None:
        Path(state.WORKSPACE_DIR, "app.py").write_text("def greet():\n    return 'hi'\n", encoding="utf-8")
        card = state.SHARED_BOARD["In Progress"].pop()
        state.SHARED_BOARD["Done"].append(card)

    result = run_eval_task(SIMPLE_TASK, step_runner=runner, workspace_root=tmp_path)
    assert result.passed is True
    assert result.verify_passed is True
    assert result.reached_done is True
    assert result.tampered is False
    assert result.steps_run == 1


def test_run_eval_task_fails_when_nothing_changes(tmp_path: Path):
    result = run_eval_task(SIMPLE_TASK, step_runner=lambda _b: None, workspace_root=tmp_path)
    assert result.passed is False
    assert "unchanged" in result.failure_reason


def test_run_eval_task_flags_test_tampering(tmp_path: Path):
    """Rewriting the verify test must never count as a pass."""

    def cheating_runner(_brief: str) -> None:
        Path(state.WORKSPACE_DIR, "app.py").write_text("def greet():\n    return 'nope'\n", encoding="utf-8")
        Path(state.WORKSPACE_DIR, "test_app.py").write_text("def test():\n    assert True\n", encoding="utf-8")

    result = run_eval_task(SIMPLE_TASK, step_runner=cheating_runner, workspace_root=tmp_path)
    assert result.tampered is True
    assert result.passed is False
    assert "protected file" in result.failure_reason


def test_run_eval_task_survives_a_crashing_step(tmp_path: Path):
    def boom(_brief: str) -> None:
        raise RuntimeError("ollama exploded")

    result = run_eval_task(SIMPLE_TASK, step_runner=boom, workspace_root=tmp_path)
    assert result.passed is False
    assert "RuntimeError" in result.failure_reason


def test_run_eval_task_restores_global_state(tmp_path: Path):
    before_ws = state.WORKSPACE_DIR
    before_board = state.SHARED_BOARD
    run_eval_task(SIMPLE_TASK, step_runner=lambda _b: None, workspace_root=tmp_path)
    assert state.WORKSPACE_DIR == before_ws
    assert state.SHARED_BOARD is before_board


def test_run_eval_task_stops_on_needs_user(tmp_path: Path):
    def escalate(_brief: str) -> None:
        if state.SHARED_BOARD["In Progress"]:
            card = state.SHARED_BOARD["In Progress"].pop()
            state.SHARED_BOARD["Needs User"].append(card)

    task = EvalTask(**{**SIMPLE_TASK.__dict__, "max_steps": 5})
    result = run_eval_task(task, step_runner=escalate, workspace_root=tmp_path)
    assert result.steps_run == 1
    assert "Needs User" in result.failure_reason


def test_economics_from_diagnostics_counts_tool_recovery():
    payload = {
        "exitReason": "max_iterations",
        "ok": False,
        "durationMs": 12000,
        "llmIterations": {"used": 6, "max": 6},
        "toolsLog": [{"toolName": "read_file"}, {"toolName": "apply_patch"}],
        "toolFailures": 1,
        "ollamaCallCount": 6,
        "ollamaMsTotal": 10000,
        "evalTokensTotal": 500,
        "promptTokensTotal": 4000,
        "tokensReported": True,
        "events": [
            {"kind": "tool_calls_recovered_from_content", "message": "read_file"},
            {"kind": "tool_calls_recovered_from_content", "message": "apply_patch"},
            {"kind": "plan_rejected", "message": "x"},
        ],
        "planRejections": 1,
        "textRejections": 0,
    }
    econ = economics_from_diagnostics(payload)
    assert econ.exit_reason == "max_iterations"
    assert econ.tool_calls == 2
    assert econ.tool_recovery_events == 2
    assert econ.tokens_per_sec == 50.0


def test_tokens_per_sec_is_none_when_not_reported():
    econ = economics_from_diagnostics({"evalTokensTotal": 100, "ollamaMsTotal": 1000, "tokensReported": False})
    assert econ.tokens_per_sec is None


def test_summarize_reports_budget_and_recovery_rates(tmp_path: Path):
    def good(_brief: str) -> None:
        Path(state.WORKSPACE_DIR, "app.py").write_text("def greet():\n    return 'hi'\n", encoding="utf-8")

    passing = run_eval_task(SIMPLE_TASK, step_runner=good, workspace_root=tmp_path / "a")
    failing = run_eval_task(SIMPLE_TASK, step_runner=lambda _b: None, workspace_root=tmp_path / "b")

    # Inject synthetic step economics; live runs get these from diagnostics JSON.
    failing.steps = [economics_from_diagnostics({"exitReason": "max_iterations", "events": []})]
    passing.steps = [
        economics_from_diagnostics(
            {"exitReason": "completed_with_writes", "events": [{"kind": "tool_calls_recovered_from_content"}]}
        )
    ]

    summary = summarize([passing, failing])
    assert summary["tasks"] == 2
    assert summary["passed"] == 1
    assert summary["passRate"] == 0.5
    assert summary["budgetExhaustedSteps"] == 1
    assert summary["toolRecoverySteps"] == 1
    assert summary["exitReasons"]["max_iterations"] == 1

    report = format_report([passing, failing], summary)
    assert "1/2 passed" in report
    assert "demo" in report


def test_golden_task_files_are_valid_json():
    from backend.services.eval_harness import DEFAULT_TASKS_DIR

    for path in DEFAULT_TASKS_DIR.glob("*.json"):
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["id"] == path.stem, f"{path.name}: id must match filename"
