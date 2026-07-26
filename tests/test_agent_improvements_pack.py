"""Tests for the full agent improvements pack (P1–P4)."""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import MagicMock, patch

from backend import state
from backend.agents.task_context import build_task_prompt, init_new_task
from backend.bootstrap import initialize
from backend.services.board_status_digest import build_board_status_digest
from backend.services.command_result import resolve_command_timeout
from backend.services.fix_verify_loop import run_fix_verify_loop
from backend.services.phone_notify import notify_if_enabled
from backend.services.prompt_budget import resolve_ollama_num_ctx
from backend.services.workflow_settings import (
    DEFAULT_WORKFLOW_SETTINGS,
    reset_workflow_settings,
    save_workflow_settings,
)


def _empty_board():
    state.SHARED_BOARD.clear()
    for lane in (
        "Backlog",
        "In Progress",
        "Needs User",
        "Needs PO",
        "QA",
        "Done",
        "Refinement",
        "Code Review",
        "Blocked",
    ):
        state.SHARED_BOARD[lane] = []


def test_prompt_contains_last_step_outcome():
    initialize()
    _empty_board()
    task = init_new_task(
        {
            "id": "T-OUT",
            "title": "Outcome inject",
            "description": "d",
            "status": "In Progress",
            "lastStepOutcome": {
                "stopReason": "max_iterations",
                "exitReason": "max_iterations",
                "whyCardStayed": "Hit iteration cap after apply_patch",
                "suggestedAction": "Extend the step (+4/+8 iterations) to continue",
                "toolsUsed": ["read_file", "apply_patch"],
            },
            "lastDiagnosis": {
                "problem": "Lint errors in main.dart",
                "rootCause": "tests",
            },
        }
    )
    state.SHARED_BOARD["In Progress"] = [task]
    prompt = build_task_prompt(task, "Brief")
    assert "=== LAST STEP OUTCOME ===" in prompt
    assert "Previous Dev step stopped: max_iterations" in prompt
    assert "Do next:" in prompt
    assert "Do not:" in prompt
    assert "apply_patch" in prompt
    assert "Extend the step" not in prompt
    assert "suggestedAction:" not in prompt
    assert "=== LAST DIAGNOSIS ===" in prompt
    assert "Lint errors" in prompt
    assert "Act on this diagnosis before exploring unrelated files." in prompt


def test_continuation_strips_last_step_outcome():
    initialize()
    _empty_board()
    from backend.services.prompt_retry import build_continuation_prompt

    task = init_new_task(
        {
            "id": "T-CONT",
            "title": "Continue",
            "description": "d",
            "status": "In Progress",
            "lastStepOutcome": {
                "stopReason": "max_iterations",
                "whyCardStayed": "Hit cap",
                "suggestedAction": "Extend the step",
                "toolsUsed": ["read_file"],
            },
            "files": [{"path": "a.py", "action": "written"}],
        }
    )
    state.SHARED_BOARD["In Progress"] = [task]
    prompt = build_continuation_prompt(
        task,
        "Brief",
        {
            "toolsUsed": ["read_file", "write_file"],
            "iterationsUsed": 8,
            "iterationsMax": 8,
            "planRejections": 1,
            "textRejections": 0,
        },
    )
    assert "CONTINUATION" in prompt
    assert "=== LAST STEP OUTCOME ===" not in prompt
    assert "Extend the step" not in prompt


def test_auto_extend_once_on_max_iter_with_writes():
    initialize()
    _empty_board()
    reset_workflow_settings()
    save_workflow_settings({"autoExtendOnMaxIter": True, "autoExtendExtraIterations": 4})
    task = init_new_task(
        {
            "id": "T-EXT",
            "title": "Auto extend",
            "description": "d",
            "status": "In Progress",
            "files": [{"path": "a.py", "action": "written"}],
        }
    )
    state.SHARED_BOARD["In Progress"] = [task]
    state.LAST_STEP_PROGRESS = {
        "toolsUsed": ["write_file", "read_file"],
        "iterationsUsed": 8,
        "iterationsMax": 8,
    }
    state.LAST_STEP_OUTCOME = {"stopReason": "max_iterations", "exitReason": "max_iterations"}

    from backend.services.sprint_service import _maybe_auto_extend_dev_step

    calls: list = []

    def fake_extend(*a, **k):
        calls.append(k)
        return {"ok": True, "output": "Extended OK", "action": "extend"}

    with patch("backend.services.prompt_retry.extend_agent_step", side_effect=fake_extend):
        out = _maybe_auto_extend_dev_step(
            "T-EXT",
            task,
            "Max tool iterations (8) reached without applying a code change.",
        )
    assert out == "Extended OK"
    assert task.get("autoExtendUsed") is True
    assert len(calls) == 1

    # Second call must not extend again
    with patch("backend.services.prompt_retry.extend_agent_step", side_effect=fake_extend):
        out2 = _maybe_auto_extend_dev_step(
            "T-EXT",
            task,
            "Max tool iterations (8) reached without applying a code change.",
        )
    assert out2.startswith("Max tool iterations")
    assert len(calls) == 1


def test_auto_extend_skipped_on_duplicate_tool():
    initialize()
    _empty_board()
    reset_workflow_settings()
    save_workflow_settings({"autoExtendOnMaxIter": True})
    task = init_new_task(
        {
            "id": "T-DUP",
            "title": "No extend",
            "description": "d",
            "status": "In Progress",
        }
    )
    state.SHARED_BOARD["In Progress"] = [task]
    state.LAST_STEP_PROGRESS = {
        "toolsUsed": ["run_command"],
        "stopReason": "duplicate_tool",
    }

    from backend.services.sprint_service import _maybe_auto_extend_dev_step

    with patch("backend.services.prompt_retry.extend_agent_step") as mock_ext:
        out = _maybe_auto_extend_dev_step(
            "T-DUP",
            task,
            "Max tool iterations (8) reached without applying a code change.",
        )
    assert out.startswith("Max tool iterations")
    mock_ext.assert_not_called()


def test_notify_kinds_registered():
    initialize()
    reset_workflow_settings()
    save_workflow_settings(
        {
            "phoneNotifyEnabled": True,
            "phoneNotifyDiscordWebhookUrl": "https://discord.com/api/webhooks/1/x",
            "phoneNotifyOnStuckEscalation": True,
            "phoneNotifyOnStepTimeout": True,
            "phoneNotifyOnBackupArmed": True,
        }
    )
    from backend.services import phone_notify

    called: list = []

    def capture(kind, *a, **k):
        called.append(kind)

    with patch.object(phone_notify, "notify_event", side_effect=capture):
        notify_if_enabled("stuck_escalation", "t", "b", task_id="t1")
        notify_if_enabled("step_timeout", "t", "b", task_id="t1")
        notify_if_enabled("backup_armed", "t", "b", task_id="t1")
    assert called == ["stuck_escalation", "step_timeout", "backup_armed"]
    assert DEFAULT_WORKFLOW_SETTINGS.get("phoneNotifyOnStuckEscalation") is True
    assert DEFAULT_WORKFLOW_SETTINGS.get("phoneNotifyOnStepTimeout") is True
    assert DEFAULT_WORKFLOW_SETTINGS.get("phoneNotifyOnBackupArmed") is True


def test_digest_includes_stuck_loops_and_blocked():
    board = {
        "Backlog": [],
        "In Progress": [{"id": "T1", "title": "Doing", "stuckLoops": 2}],
        "Blocked": [{"id": "T2", "title": "Wait"}],
        "Needs User": [],
        "Done": [],
    }
    text = build_board_status_digest(
        board=board,
        project_name="Demo",
        active_task={
            "id": "T1",
            "title": "Doing",
            "stuckLoops": 2,
            "lastStepOutcome": {"stopReason": "max_iterations"},
            "backupModelStepsRemaining": {"dev": 1},
        },
        handler="dev",
        agent="Developer",
    )
    assert "stuckLoops=2" in text
    assert "stop=max_iterations" in text
    assert "backupRemaining=1" in text
    assert "Blocked: 1" in text


def test_command_timeout_uses_remaining_step_budget(monkeypatch):
    initialize()
    reset_workflow_settings()
    save_workflow_settings(
        {"terminalTimeoutSec": 120, "maxAgentStepDurationSec": 600}
    )
    # Step started 60s ago → remaining 540 → budget max(120, min(510, 1800)) = 510
    state.SPRINT_STEP_STARTED_MONO = time.monotonic() - 60
    to = resolve_command_timeout("pytest -q")
    assert to == 510
    state.SPRINT_STEP_STARTED_MONO = None


def test_terminal_timeout_default_600():
    assert DEFAULT_WORKFLOW_SETTINGS.get("terminalTimeoutSec") == 600


def test_fix_verify_runs_when_require_clean_lint_alone():
    initialize()
    reset_workflow_settings()
    save_workflow_settings({"enableFixVerifyLoop": False, "requireCleanLint": True})

    agent = MagicMock()
    agent.execute_step.return_value = "done"
    task = {"id": "T-FV", "title": "fv"}

    with patch(
        "backend.services.fix_verify_loop.derive_project_lint_command",
        return_value=None,
    ):
        # No lint command → falls through to execute_step once (still gated as enabled)
        out = run_fix_verify_loop(agent, task, "prompt", max_iterations=4)
    assert out == "done"
    agent.execute_step.assert_called_once()


def test_resolve_ollama_num_ctx_by_role():
    ws = {
        "ollamaNumCtx": 32768,
        "ollamaNumCtxByRole": {},
        "ollamaNumCtxAuto": False,
    }
    assert resolve_ollama_num_ctx("dev", settings=ws) == 32768
    assert resolve_ollama_num_ctx("po", settings=ws) == 16384
    ws["ollamaNumCtxByRole"] = {"po": 8192}
    assert resolve_ollama_num_ctx("po", settings=ws) == 8192


def test_resolve_ollama_num_ctx_auto_halves_dev_on_low():
    ws = {
        "ollamaNumCtx": 32768,
        "ollamaNumCtxByRole": {},
        "ollamaNumCtxAuto": True,
    }
    with patch(
        "backend.services.system_capacity.probe_system_capacity",
        return_value={"tier": "low"},
    ):
        assert resolve_ollama_num_ctx("dev", settings=ws) == 16384


def test_ui_diagnostics_and_settings_markers():
    root = Path(__file__).resolve().parents[1]
    modal = (root / "frontend" / "src" / "components" / "TaskDetailModal.tsx").read_text(
        encoding="utf-8"
    )
    assert "LAST_STEP_DIAGNOSTICS" in modal
    assert "task-step-diagnostics" in modal
    panel = (root / "frontend" / "src" / "components" / "WorkflowPanel.tsx").read_text(
        encoding="utf-8"
    )
    assert "autoExtendOnMaxIter" in panel
    assert "phoneNotifyOnStuckEscalation" in panel
    assert "enableVramAwareModelSwap" in panel
    assert "Long builds use remaining step time" in panel
    readme = (root / "README.md").read_text(encoding="utf-8")
    assert "autoExtendOnMaxIter" in readme
    assert "phoneNotifyOnBackupArmed" in readme
    assert "ollamaNumCtxByRole" in readme


def test_mid_step_backup_marker_in_scrum_agent():
    root = Path(__file__).resolve().parents[1]
    src = (root / "backend" / "agents" / "scrum_agent.py").read_text(encoding="utf-8")
    assert "Mid-step backup switch" in src
    assert "_mid_step_backup_switched" in src
