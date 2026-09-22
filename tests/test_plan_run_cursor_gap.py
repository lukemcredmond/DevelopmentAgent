"""Plan & Run cursor-gap features: implementer default, preflight, rules, card session."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from backend import state
from backend.bootstrap import initialize
from backend.services.implementer_profile import (
    implementer_gate_relaxation_active,
    resolve_plan_run_execution_profile,
)
from backend.services.plan_run_orchestration import (
    apply_plan_run_execution_profile,
    brief_is_actionable,
    ensure_implementer_card_from_brief,
    should_skip_po_plan_for_plan_run,
)
from backend.services.plan_run_preflight import validate_plan_run_preflight
from backend.services.workflow_settings import reset_workflow_settings, save_workflow_settings


def teardown_function():
    reset_workflow_settings()
    state.AUTO_SPRINT_ACTIVE = False


def test_brief_is_actionable():
    assert not brief_is_actionable("too short")
    assert brief_is_actionable(
        "Implement a health check endpoint for the API with tests and lint clean output."
    )


def test_plan_run_defaults_to_implementer_profile():
    reset_workflow_settings()
    assert resolve_plan_run_execution_profile() == "implementer"


def test_apply_plan_run_execution_profile_persists():
    initialize()
    reset_workflow_settings()
    save_workflow_settings({"executionProfile": "scrum"})
    profile = apply_plan_run_execution_profile(persist=True)
    assert profile == "implementer"
    from backend.services.workflow_settings import get_execution_profile

    assert get_execution_profile() == "implementer"


def test_skip_po_when_actionable_brief():
    reset_workflow_settings()
    brief = "Fix the login bug: validate JWT expiry and add pytest coverage for edge cases."
    assert should_skip_po_plan_for_plan_run(brief)


def test_ensure_implementer_card_from_brief():
    initialize()
    reset_workflow_settings()
    state.SHARED_BOARD["Backlog"] = []
    ok, task_id = ensure_implementer_card_from_brief(
        "Implement user profile page with avatar upload and form validation tests."
    )
    assert ok is True
    assert task_id
    assert len(state.SHARED_BOARD.get("Backlog") or []) == 1


def test_implementer_gate_relaxation_when_auto_sprint():
    reset_workflow_settings()
    save_workflow_settings({"executionProfile": "implementer"})
    state.AUTO_SPRINT_ACTIVE = False
    assert implementer_gate_relaxation_active() is True
    state.AUTO_SPRINT_ACTIVE = True
    assert implementer_gate_relaxation_active() is True


def test_workflow_defaults_cursor_like():
    reset_workflow_settings()
    from backend.services.workflow_settings import get_workflow_settings

    ws = get_workflow_settings()
    assert ws.get("executionProfile") == "implementer"
    assert ws.get("enableSystemAutoVerify") is True
    assert ws.get("maxLlmIterationsPerStep") == 12
    assert ws.get("devExploreMaxTools") == 1


@patch("backend.services.llm_provider.get_chat_provider")
def test_preflight_blocks_unreachable_llm(mock_provider):
    initialize()
    reset_workflow_settings()
    health = MagicMock()
    health.ok = False
    health.error = "connection refused"
    health.url = "http://localhost:11434"
    mock_provider.return_value.health.return_value = health
    result = validate_plan_run_preflight(
        "Implement a small API fix with unit tests and lint clean.",
    )
    assert result["blocked"] is True
    assert result["ok"] is False


def test_workspace_rules_load(tmp_path):
    from backend.services.workspace_rules import load_workspace_rules_context

    root = tmp_path / "ws"
    root.mkdir()
    (root / "AGENTS.md").write_text("# Rules\nUse pytest for tests.\n", encoding="utf-8")
    state.WORKSPACE_DIR = str(root)
    text = load_workspace_rules_context(max_chars=5000)
    assert "Use pytest" in text
