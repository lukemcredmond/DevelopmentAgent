"""Cross-step tool fingerprints and sprint context compress."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from backend.agents.scrum_agent import SAME_ARGS_SUCCESS_LIMIT
from backend.agents.task_context import build_task_prompt, init_new_task
from backend.agents.tool_fingerprints import (
    block_tool_fingerprint_on_task,
    finalize_step_tool_fingerprints,
    fingerprint_overlap_ratio,
    seed_tool_keys_from_task,
    should_escalate_repeat_tool_overlap,
)
from backend.bootstrap import initialize


def test_seed_blocked_fingerprint_soft_skips_on_first_repeat():
    initialize()
    task = init_new_task({"id": "T-FP", "title": "t", "description": "d"})
    block_tool_fingerprint_on_task(task, "run_command", {"command": "flutter analyze"})
    success, _failed = seed_tool_keys_from_task(task)
    assert len(success) == 1
    assert success[0][0] == "run_command"
    key = success[0]
    assert success.count(key) == 1
    assert success.count(key) < SAME_ARGS_SUCCESS_LIMIT - 1


def test_last_step_outcome_lists_blocked_tools():
    initialize()
    from backend import state

    task = init_new_task({"id": "T-BLK", "title": "t", "description": "d"})
    block_tool_fingerprint_on_task(task, "read_file", {"path": "lib/main.dart"})
    state.SHARED_BOARD = {"In Progress": [task]}
    task["lastStepOutcome"] = {"stopReason": "duplicate_tool", "toolsUsed": ["read_file"]}
    prompt = build_task_prompt(task, "brief")
    assert "Do not call these again" in prompt
    assert "read_file" in prompt


def test_finalize_step_rotates_fingerprint_labels():
    task = {"id": "T-ROT", "title": "t", "lastStepToolFingerprints": ["a"], "priorStepToolFingerprints": []}
    keys = [("run_command", '{"command": "npm test"}')]
    finalize_step_tool_fingerprints(task, keys, stop_reason="max_iterations")
    assert "run_command" in str(task.get("lastStepToolFingerprints"))
    assert task.get("priorStepToolFingerprints") == ["a"]


def test_overlap_escalation_detects_repeat_stuck():
    shared = ["run_command(npm test)", "read_file(src/main.ts)"]
    task = {
        "id": "T-OV",
        "lastStepToolFingerprints": shared,
        "priorStepToolFingerprints": list(shared),
    }
    assert fingerprint_overlap_ratio(task["lastStepToolFingerprints"], task["priorStepToolFingerprints"]) >= 0.65
    assert should_escalate_repeat_tool_overlap(task) is True


def test_context_compress_skips_under_min_chars():
    from backend.services.context_compress import maybe_compress_sprint_context_block

    short = "=== SEMANTIC ===\n" + ("x" * 100)
    assert maybe_compress_sprint_context_block(short) == short


def test_context_compress_replaces_when_ollama_returns():
    from backend.services.context_compress import maybe_compress_sprint_context_block

    long_block = "=== PRE-LOADED ===\n" + ("line\n" * 2000)
    mock_resp = MagicMock()
    mock_resp.message.content = "compressed paths and errors only"
    with patch("backend.services.context_compress.get_workflow_settings") as gw:
        gw.return_value = {
            "enableLlmContextCompress": True,
            "contextCompressMinChars": 100,
            "contextCompressMaxChars": 500,
            "contextCompressModel": "fast-model",
            "ollamaRequestTimeoutSec": 30,
            "discordModelPresetFast": "",
        }
        with patch("ollama.Client") as client_cls:
            client_cls.return_value.chat.return_value = mock_resp
            out = maybe_compress_sprint_context_block(long_block, agent_role="Developer")
    assert "COMPRESSED WORKSPACE CONTEXT" in out
    assert "compressed paths" in out


def test_stuck_overlap_bumps_stuck_loops_to_max():
    initialize()
    from backend import state
    from backend.agents.task_context import init_new_task
    from backend.services.sprint_service import _check_stuck_and_escalate

    task = init_new_task({"id": "T-STK", "title": "Stuck", "description": "d"})
    task["stuckLoops"] = 0
    shared = ["write_file(app.ts)", "apply_patch(app.ts)"]
    task["lastStepToolFingerprints"] = shared
    task["priorStepToolFingerprints"] = list(shared)
    state.SHARED_BOARD = {"In Progress": [task]}
    with patch("backend.services.sprint_service.get_workflow_settings") as gw:
        gw.return_value = {
            "maxStuckSteps": 3,
            "enableSplitOnStuck": False,
            "enableBackupModelOnStuck": False,
            "maxPoRoundTrips": 3,
        }
        with patch("backend.services.backup_model.arm_backup_for_agent"):
            with patch("backend.services.sprint_service.stuck_is_tool_or_lint", return_value=False):
                with patch("backend.services.sprint_service.increment_po_round_trips"):
                    with patch("backend.services.sprint_service.publish_activity"):
                        with patch("backend.services.sprint_service.move_board_stage") as move:
                            _check_stuck_and_escalate("T-STK", "In Progress", agent_key="dev")
                            assert move.called
