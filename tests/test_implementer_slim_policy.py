"""Implementer slim prompt policy (recovery_only vs always)."""

from unittest.mock import patch

from backend.services.sprint_service import _should_slim_dev_prompt


def test_implementer_recovery_only_not_slim_on_clean_card():
    task = {"id": "T1", "title": "Add feature", "consecutiveBadExits": 0}
    ws = {"executionProfile": "implementer", "implementerSlimPrompt": "recovery_only"}

    with patch("backend.services.sprint_service.get_workflow_settings", return_value=ws):
        with patch(
            "backend.services.workflow_settings.get_execution_profile",
            return_value="implementer",
        ):
            assert _should_slim_dev_prompt(task) is False


def test_implementer_recovery_only_slim_on_force_patch():
    task = {"id": "T1", "forcePatchNextDevStep": True}
    ws = {"executionProfile": "implementer", "implementerSlimPrompt": "recovery_only"}

    with patch("backend.services.sprint_service.get_workflow_settings", return_value=ws):
        with patch(
            "backend.services.workflow_settings.get_execution_profile",
            return_value="implementer",
        ):
            assert _should_slim_dev_prompt(task) is True


def test_needs_po_circuit_allows_one_retry():
    from backend.services.sprint_speed_gates import needs_po_should_skip_auto

    task = {"id": "T1", "identicalPatchFailCount": 4}
    ws = {
        "enableStuckCircuitBreaker": True,
        "circuitBreakerIdenticalPatchFails": 3,
    }
    assert needs_po_should_skip_auto(task, ws) is False
    task["circuitBreakerPoRetryUsed"] = True
    assert needs_po_should_skip_auto(task, ws) is True
