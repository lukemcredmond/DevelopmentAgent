"""Developer text-rejection loop: backup escalation before Needs User park."""

from unittest.mock import patch

from backend import state
from backend.agents.scrum_agent import ScrumAgent
from backend.agents.task_context import init_new_task
from backend.bootstrap import initialize
from pathlib import Path

from backend.services.step_diagnostics import StepDiagnosticsTracker


def test_text_rejection_park_deferred_when_force_patch_and_backup_pending():
    initialize()
    agent = ScrumAgent(role="Developer", model="primary-dev", system_prompt="dev")
    task = init_new_task(
        {
            "id": "T-FP",
            "title": "Feature",
            "description": "d",
            "status": "In Progress",
            "forcePatchNextDevStep": True,
        }
    )
    state.SHARED_BOARD.clear()
    for lane in ("Backlog", "In Progress", "Needs User", "Needs PO", "QA", "Done"):
        state.SHARED_BOARD[lane] = []
    state.SHARED_BOARD["In Progress"] = [task]

    with patch("backend.services.backup_model.backup_model", return_value="backup-dev"), patch(
        "backend.services.backup_model.primary_model", return_value="primary-dev"
    ), patch(
        "backend.services.backup_model.dev_backup_model_allowed", return_value=(True, "")
    ), patch(
        "backend.services.workflow_settings.get_workflow_settings",
        return_value={"enableBackupModelOnStuck": True},
    ):
        assert agent._text_rejection_park_allowed("T-FP") is False

    agent._note_backup_switch_outcome("blocked:benchmark")
    assert agent._text_rejection_park_allowed("T-FP") is True


def test_forced_patch_refusal_triggers_backup_switch():
    initialize()
    agent = ScrumAgent(role="Developer", model="primary-dev", system_prompt="dev")
    agent._forced_patch_step = True
    called = {"n": 0}

    def fake_switch(**kwargs):
        called["n"] += 1
        return True

    with patch.object(agent, "_switch_dev_backup_model", side_effect=fake_switch):
        agent._maybe_switch_backup_on_forced_patch_refusal(
            task_id="T-1",
            plan_n=0,
            text_n=1,
            reason="forced-patch Developer text-only reject",
            content="THOUGHT: planning only",
        )
    assert called["n"] == 1


def test_step_diagnostics_records_backup_and_edit_target():
    trace = StepDiagnosticsTracker(
        task_id="t",
        task_title="title",
        agent="Developer",
        lane_before="In Progress",
        file_path=Path("/tmp/trace.json"),
    )
    trace.note_resolved_edit_target("lib/main.dart")
    trace.note_backup_switch_outcome("switched")
    trace.note_text_reject_hash("abc123", 2)
    payload = trace._build_payload(status="running")
    assert payload["resolvedEditTarget"] == "lib/main.dart"
    assert payload["backupSwitchOutcome"] == "switched"
    assert payload["textRejectContentHash"] == "abc123"
    assert payload["textRejectContentHashCount"] == 2
    assert trace.identical_text_reject_count >= 1
