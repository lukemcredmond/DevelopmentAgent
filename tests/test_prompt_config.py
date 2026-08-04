"""Tests for per-project agent prompt defaults and overrides."""

from backend.agents.registry import agent_dev, configure_agent_prompts, configure_agent_tools
from backend.services.prompt_defaults import (
    DEFAULT_AGENT_SYSTEM,
    clear_agent_prompt_overrides,
    format_step_instructions,
    get_effective_step_instructions,
    get_effective_system_prompt,
    has_prompt_override,
    registered_tool_names_for_role,
    validate_agent_prompts_patch,
)
from backend.services.workflow_settings import reset_workflow_settings, restore_agent_prompt_overrides, save_workflow_settings


def test_get_effective_system_prompt_uses_default():
    assert get_effective_system_prompt("Developer", {}) == DEFAULT_AGENT_SYSTEM["Developer"]


def test_get_effective_system_prompt_uses_override():
    settings = {"agentPrompts": {"Developer": {"system": "Custom dev system", "stepInstructions": None}}}
    assert get_effective_system_prompt("Developer", settings) == "Custom dev system"


def test_format_step_instructions_substitutes_placeholders():
    out = format_step_instructions("Lint{lint_hint} max {max_in_card_lint}", {"lint_hint": " (x)", "max_in_card_lint": 3})
    assert out == "Lint (x) max 3"


def test_format_step_instructions_unknown_placeholder_preserved():
    out = format_step_instructions("Keep {unknown}", {})
    assert out == "Keep {unknown}"


def test_get_effective_step_instructions_dev_default_contains_safety_lines():
    reset_workflow_settings()
    configure_agent_tools({})
    text = get_effective_step_instructions(
        "Developer",
        {},
        {"lint_hint": "", "max_in_card_lint": 5, "target_lane": "QA", "autonomous_suffix": ""},
    )
    assert "Needs PO" in text
    assert "apply_patch" in text
    assert "Registered tools:" in text
    for name in registered_tool_names_for_role("Developer").split(", "):
        assert name in text


def test_get_effective_step_instructions_registered_tools_match_agent_tools_tab():
    reset_workflow_settings()
    try:
        configure_agent_tools(
            {
                "agentTools": {
                    "Developer": ["read_file", "grep", "update_board"],
                },
                "enableSemanticSearch": False,
                "enableWebSearch": False,
            }
        )
        expected = "grep, read_file, update_board"
        assert registered_tool_names_for_role("Developer") == expected
        text = get_effective_step_instructions(
            "Developer",
            {},
            {"lint_hint": "", "max_in_card_lint": 5, "target_lane": "QA", "autonomous_suffix": ""},
        )
        assert f"Registered tools: {expected}." in text
        assert "git_commit" not in text.split("Registered tools:")[-1]
    finally:
        configure_agent_tools({})


def test_get_effective_step_instructions_override():
    reset_workflow_settings()
    configure_agent_tools({})
    settings = {
        "agentPrompts": {
            "Developer": {"system": None, "stepInstructions": "Only do {target_lane}"},
        }
    }
    out = get_effective_step_instructions("Developer", settings, {"target_lane": "Done"})
    assert out.startswith("Only do Done")
    assert "Registered tools:" in out
    assert "read_file" in out


def test_clear_agent_prompt_overrides_one_role():
    settings = {
        "agentPrompts": {
            "Developer": {"system": "x", "stepInstructions": "y"},
            "QA Tester": {"system": "q", "stepInstructions": None},
        }
    }
    cleared = clear_agent_prompt_overrides(settings, role="Developer")
    assert cleared["agentPrompts"]["Developer"]["system"] is None
    assert cleared["agentPrompts"]["QA Tester"]["system"] == "q"


def test_configure_agent_prompts_applies_override():
    configure_agent_prompts({"agentPrompts": {"Developer": {"system": "Temp override", "stepInstructions": None}}})
    try:
        assert agent_dev.system_prompt == "Temp override"
    finally:
        configure_agent_prompts({})


def test_validate_agent_prompts_patch_rejects_huge_text():
    try:
        validate_agent_prompts_patch(
            {"agentPrompts": {"Developer": {"system": "x" * 20000, "stepInstructions": None}}}
        )
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_has_prompt_override():
    assert not has_prompt_override({}, "Developer", "system")
    assert has_prompt_override(
        {"agentPrompts": {"Developer": {"system": "a", "stepInstructions": None}}},
        "Developer",
        "system",
    )


def test_restore_agent_prompt_overrides_persists(monkeypatch):
    stored = {}

    def fake_set(key, val):
        stored[key] = val

    def fake_get(key):
        return stored.get(key)

    monkeypatch.setattr("backend.services.workflow_settings.state.storage.set_setting", fake_set)
    monkeypatch.setattr("backend.services.workflow_settings.state.storage.get_setting", fake_get)
    monkeypatch.setattr("backend.services.workflow_settings.state.CURRENT_PROJECT_ID", "p1")

    save_workflow_settings(
        {"agentPrompts": {"Developer": {"system": "custom", "stepInstructions": None}}},
        project_id="p1",
    )
    merged = restore_agent_prompt_overrides(project_id="p1", role="Developer")
    assert merged["agentPrompts"]["Developer"]["system"] is None
