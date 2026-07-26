"""Per-agent backup model arming / apply / restore on stuck."""

from __future__ import annotations

from backend.bootstrap import initialize
from backend.agents.task_context import init_new_task
from backend.services.backup_model import (
    apply_model_for_step,
    arm_backup_for_agent,
    should_arm_from_exit_reason,
)
from backend.services.workflow_settings import reset_workflow_settings, save_workflow_settings


class FakeAgent:
    def __init__(self, model: str = "primary:7b"):
        self.model = model


def test_should_arm_from_exit_reason():
    assert should_arm_from_exit_reason("read_only_no_edits")
    assert should_arm_from_exit_reason("plan_exhausted")
    assert not should_arm_from_exit_reason("completed_with_writes")
    assert not should_arm_from_exit_reason(None)


def test_arm_and_apply_then_restore():
    from backend import state

    initialize()
    reset_workflow_settings()
    save_workflow_settings({"enableBackupModelOnStuck": True, "backupModelStuckSteps": 2})
    state.PRIMARY_MODELS = {**state.PRIMARY_MODELS, "dev": "primary-dev"}
    state.BACKUP_MODELS = {**state.BACKUP_MODELS, "dev": "backup-dev"}

    task = init_new_task({"id": "T-BU", "title": "Stuck", "description": "d"})
    agent = FakeAgent("primary-dev")

    assert arm_backup_for_agent("dev", task, reason="read_only_no_edits") is True
    assert task["backupModelStepsRemaining"]["dev"] == 2

    used = apply_model_for_step(agent, "dev", task)
    assert used == "backup-dev"
    assert agent.model == "backup-dev"
    assert task["backupModelStepsRemaining"]["dev"] == 1

    used2 = apply_model_for_step(agent, "dev", task)
    assert used2 == "backup-dev"
    assert task["backupModelStepsRemaining"]["dev"] == 0

    used3 = apply_model_for_step(agent, "dev", task)
    assert used3 == "primary-dev"
    assert agent.model == "primary-dev"


def test_no_arm_when_backup_empty_or_same():
    from backend import state

    initialize()
    reset_workflow_settings()
    save_workflow_settings({"enableBackupModelOnStuck": True})
    state.PRIMARY_MODELS = {**state.PRIMARY_MODELS, "dev": "same"}
    state.BACKUP_MODELS = {**state.BACKUP_MODELS, "dev": ""}
    task = init_new_task({"id": "T-NB", "title": "t", "description": "d"})
    assert arm_backup_for_agent("dev", task, reason="stuck") is False

    state.BACKUP_MODELS["dev"] = "same"
    assert arm_backup_for_agent("dev", task, reason="stuck") is False


def test_no_arm_for_lint_tool_stuck():
    from backend import state

    initialize()
    reset_workflow_settings()
    save_workflow_settings({"enableBackupModelOnStuck": True})
    state.PRIMARY_MODELS = {**state.PRIMARY_MODELS, "dev": "primary-dev"}
    state.BACKUP_MODELS = {**state.BACKUP_MODELS, "dev": "backup-dev"}
    task = init_new_task({"id": "T-LINT", "title": "t", "description": "d"})
    task["lastCommandDiagnostics"] = [
        {"file": "a.dart", "line": 1, "column": 1, "severity": "error", "message": "x"}
    ]
    assert arm_backup_for_agent("dev", task, reason="stuck") is False


def test_lane_move_clears_remaining():
    from backend import state
    from backend.services.sprint_service import _check_stuck_and_escalate
    from backend.agents.task_context import get_task_lane

    initialize()
    reset_workflow_settings()
    save_workflow_settings({"enableBackupModelOnStuck": True, "maxStuckSteps": 5})
    state.PRIMARY_MODELS = {**state.PRIMARY_MODELS, "dev": "primary-dev"}
    state.BACKUP_MODELS = {**state.BACKUP_MODELS, "dev": "backup-dev"}

    task = init_new_task({"id": "T-CLR", "title": "t", "description": "d"})
    state.SHARED_BOARD = {
        "In Progress": [],
        "QA": [task],
        "Backlog": [],
        "Needs PO": [],
        "Needs User": [],
        "Done": [],
        "Features": [],
        "Refinement": [],
        "Code Review": [],
    }
    task["status"] = "QA"
    arm_backup_for_agent("dev", task, reason="stuck")
    assert task["backupModelStepsRemaining"]["dev"] == 2
    # Lane changed In Progress → QA clears remaining
    _check_stuck_and_escalate("T-CLR", "In Progress", agent_key="dev")
    task = next(t for t in state.SHARED_BOARD["QA"] if t["id"] == "T-CLR")
    assert (task.get("backupModelStepsRemaining") or {}).get("dev", 0) == 0
    assert get_task_lane("T-CLR") == "QA"


def test_defaults_and_ui_markers():
    from pathlib import Path

    from backend.services.workflow_settings import DEFAULT_WORKFLOW_SETTINGS

    assert DEFAULT_WORKFLOW_SETTINGS.get("enableBackupModelOnStuck") is True
    assert DEFAULT_WORKFLOW_SETTINGS.get("backupModelStuckSteps") == 2
    root = Path(__file__).resolve().parents[1]
    panel = (root / "frontend" / "src" / "components" / "SettingsSlideOver.tsx").read_text(
        encoding="utf-8"
    )
    assert "Backup (stuck)" in panel
    wf = (root / "frontend" / "src" / "components" / "WorkflowPanel.tsx").read_text(
        encoding="utf-8"
    )
    assert "enableBackupModelOnStuck" in wf
    assert "backupModelStuckSteps" in wf
