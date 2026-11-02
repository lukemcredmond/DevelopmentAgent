"""Regression tests for sprint performance optimization plan."""

from backend.services.card_session import build_incremental_step_prompt
from backend.services.diagnostics_parser import (
    parse_command_diagnostics,
    parse_pubspec_and_resolution_errors,
)
from backend.services.duplicate_tool_policy import tools_log_has_verify
from backend.services.llm_context import estimate_messages_chars, prune_messages_if_needed
from backend.services.step_diagnostics import derive_exit_reason
from backend.services.tool_llm_probe import mark_native_tool_probe_ok, native_tool_probe_known_ok
from backend.services.workflow_settings import (
    DEFAULT_WORKFLOW_SETTINGS,
    migrate_performance_settings,
    reset_workflow_settings,
    save_workflow_settings,
)


def test_enable_card_session_summarize_default_off():
    assert DEFAULT_WORKFLOW_SETTINGS["enableCardSessionSummarize"] is False
    out = migrate_performance_settings({"performanceSettingsVersion": 2})
    assert out["enableCardSessionSummarize"] is False
    assert out["performanceSettingsVersion"] >= 3


def test_build_incremental_step_prompt_no_12k_resend():
    task = {
        "cardAgentSession": {
            "lastUserPrompt": "alpha",
        }
    }
    full = "beta completely different prompt body"
    out = build_incremental_step_prompt(task, full)
    assert "CONTINUATION" in out
    assert full[:200] not in out


def test_prune_messages_reduces_chars_with_dynamic_head():
    reset_workflow_settings()
    save_workflow_settings({"ollamaNumCtx": 4096, "messagePruneThresholdPct": 10})
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "system", "content": "hint"},
        {"role": "user", "content": "task"},
    ]
    for i in range(20):
        messages.append({"role": "tool", "tool_name": "read_file", "content": "y" * 400})
        messages.append({"role": "system", "content": f"note {i}"})
    before = estimate_messages_chars(messages)
    prune_messages_if_needed(messages)
    after = estimate_messages_chars(messages)
    assert after < before


def test_parse_pubspec_errors_from_flutter_analyze():
    out = "Error on line 12, column 3 of pubspec.yaml\nBecause project depends on flutter_sqlite"
    findings = parse_pubspec_and_resolution_errors(out)
    assert findings and findings[0]["file"] == "pubspec.yaml"


def test_parse_command_diagnostics_synthesizes_on_failed_analyze():
    output = "pub get failed\nversion solving failed"
    findings = parse_command_diagnostics("flutter analyze", output, exit_code=1)
    assert findings


def test_tools_log_has_verify_after_write_only():
    tools_log = [
        {"toolName": "apply_patch", "success": True},
        {"toolName": "system_verify", "success": True, "summary": "ok"},
    ]
    assert tools_log_has_verify(tools_log) is True
    tools_log_old_verify = [
        {"toolName": "system_verify", "success": True},
        {"toolName": "apply_patch", "success": True},
    ]
    assert tools_log_has_verify(tools_log_old_verify) is False


def test_derive_exit_reason_task_already_done():
    reason = derive_exit_reason(
        agent_result="Stopped: task already Done",
        tools_used={"apply_patch"},
        lane_before="In Progress",
        lane_after="Done",
    )
    assert reason == "completed_with_writes"


def test_native_tool_probe_cache():
    mark_native_tool_probe_ok("model-a", "read_file")
    assert native_tool_probe_known_ok("model-a", "read_file")
