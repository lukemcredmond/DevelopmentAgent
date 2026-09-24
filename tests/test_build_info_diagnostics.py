"""Diagnostics build stamp and backup model pin."""

from unittest.mock import patch

from backend.agents.scrum_agent import ScrumAgent
from backend.services.build_info import DIAGNOSTICS_SCHEMA_VERSION, get_app_build_info
from backend.services.step_diagnostics import start_step_trace


def test_get_app_build_info_has_schema_and_git():
    with patch(
        "backend.services.build_info.subprocess.check_output",
        side_effect=[b"fullhash1234567890", b"abc1234"],
    ):
        get_app_build_info.cache_clear()
        info = get_app_build_info()
    assert info["diagnosticsSchemaVersion"] == DIAGNOSTICS_SCHEMA_VERSION
    assert info["gitSha"] == "abc1234"
    assert "recoveryFeatures" in info


def test_trace_payload_includes_app_build():
    trace = start_step_trace("T-BUILD", "Title", "Developer", "In Progress")
    with patch(
        "backend.services.build_info.get_app_build_info",
        return_value={
            "diagnosticsSchemaVersion": 2,
            "gitSha": "deadbeef",
            "recoveryFeatures": ["diagnostics_build_stamp"],
        },
    ):
        payload = trace._build_payload(status="running")
    assert payload["diagnosticsSchemaVersion"] == 2
    assert payload["appBuild"]["gitSha"] == "deadbeef"


def test_apply_phase_model_routing_pins_mid_step_backup():
    agent = ScrumAgent(role="Developer", model="backup-coder", system_prompt="dev")
    agent._mid_step_backup_switched = True
    with patch.object(agent, "sync_role_primary_model", return_value="primary-14b"):
        model = agent._apply_phase_model_routing()
    assert model == "backup-coder"
    assert agent._backup_model_pinned_logged is True
