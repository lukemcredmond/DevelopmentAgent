"""Tests for LLM performance optimizations."""

from backend.bootstrap import initialize
from backend.services.llm_context import (
    estimate_messages_chars,
    prune_messages_if_needed,
    truncate_tool_output_for_llm,
)
from backend.services.workflow_settings import reset_workflow_settings, save_workflow_settings


def test_truncate_tool_output_respects_cap():
    reset_workflow_settings()
    save_workflow_settings({"maxToolOutputCharsForLlm": 500})
    long_text = "x" * 2000
    out = truncate_tool_output_for_llm("grep", long_text)
    assert len(out) <= 520
    assert "truncated" in out.lower()


def test_truncate_run_command_keeps_problems_section():
    reset_workflow_settings()
    save_workflow_settings({"maxToolOutputCharsForLlm": 800})
    text = (
        "[findings exit 1]\nflutter analyze\nSummary: 2 issues\n\n"
        "## Problems (fix these first)\n"
        "- lib/a.dart:10:5  error  missing semicolon\n"
        "- lib/b.dart:3:1  warning  unused import\n\n"
        "## Output\n"
        + ("line\n" * 500)
    )
    out = truncate_tool_output_for_llm("run_command", text)
    assert "## Problems" in out
    assert "lib/a.dart:10:5" in out
    assert len(out) <= 900


def test_prune_messages_drops_old_tools():
    reset_workflow_settings()
    save_workflow_settings({"ollamaNumCtx": 4096, "messagePruneThresholdPct": 10})
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "task"},
    ]
    for i in range(20):
        messages.append({"role": "tool", "tool_name": "read_file", "content": "y" * 400})
        messages.append({"role": "system", "content": f"hint {i}"})

    before = estimate_messages_chars(messages)
    prune_messages_if_needed(messages)
    after = estimate_messages_chars(messages)
    assert after < before
    assert messages[0]["role"] == "system"
    assert messages[1]["role"] == "user"
    assert any("Context pruned" in str(m.get("content", "")) for m in messages)


def test_chat_options_includes_keep_alive():
    from backend.agents.registry import agent_po

    initialize()
    reset_workflow_settings()
    save_workflow_settings({"ollamaKeepAlive": "45m"})
    opts = agent_po._chat_options()
    assert opts.get("keep_alive") == "45m"


def test_build_semantic_sprint_context_empty_without_index():
    from backend.storage.code_index import build_semantic_sprint_context

    initialize()
    reset_workflow_settings()
    block, paths = build_semantic_sprint_context(
        {"title": "Auth login", "description": "Implement JWT"},
        max_chars=4000,
    )
    assert block == ""
    assert paths == []


def test_workflow_settings_performance_defaults():
    from backend.services.workflow_settings import DEFAULT_WORKFLOW_SETTINGS, get_workflow_settings

    initialize()
    reset_workflow_settings()
    ws = get_workflow_settings()
    assert ws["ollamaKeepAlive"] == DEFAULT_WORKFLOW_SETTINGS["ollamaKeepAlive"]
    assert ws["maxToolOutputCharsForLlm"] == 6000
    assert ws["enableSemanticSprintContext"] is True
