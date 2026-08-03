"""Duplicate skip: seed exempt read tools + replay prior output."""

from backend.agents.task_context import init_new_task
from backend.agents.tool_fingerprints import seed_tool_keys_from_task, tool_fingerprint_key
from backend.services.duplicate_tool_policy import filter_tool_keys_for_in_step_seed
from backend.services.tool_cache import (
    find_prior_tool_output_for_task,
    format_duplicate_skip_output,
)


def test_filter_tool_keys_excludes_read_file_from_seed():
    key = tool_fingerprint_key("read_file", {"path": "lib/main.dart"})
    patch = tool_fingerprint_key("apply_patch", {"path": "x", "old_text": "a", "new_text": "b"})
    task = init_new_task({"id": "T-SEED", "title": "x", "description": "d"})
    from backend.agents.tool_fingerprints import record_tool_fingerprint_on_task

    record_tool_fingerprint_on_task(task, "read_file", {"path": "lib/main.dart"})
    record_tool_fingerprint_on_task(task, "apply_patch", {"path": "x", "old_text": "a", "new_text": "b"})
    seeded, _ = seed_tool_keys_from_task(task)
    filtered = filter_tool_keys_for_in_step_seed(seeded)
    tools = {k[0] for k in filtered}
    assert "read_file" not in tools
    assert "apply_patch" in tools


def test_find_prior_tool_output_from_transcript():
    task = init_new_task({"id": "T-OUT", "title": "x", "description": "d"})
    task["transcript"] = [
        {
            "toolName": "read_file",
            "toolSuccess": True,
            "toolArgs": {"path": "foo.txt"},
            "toolOutput": "file body line 1",
        }
    ]
    out = find_prior_tool_output_for_task(task, "read_file", {"path": "foo.txt"})
    assert out == "file body line 1"


def test_format_duplicate_skip_replays_read_file():
    task = init_new_task({"id": "T-REP", "title": "x", "description": "d"})
    task["transcript"] = [
        {
            "toolName": "list_dir",
            "toolSuccess": True,
            "toolArgs": {"path": "."},
            "toolOutput": "dir listing here",
        }
    ]
    msg = format_duplicate_skip_output(
        "list_dir",
        arguments={"path": "."},
        task=task,
        fallback_summary=".",
    )
    assert "prior result replayed" in msg
    assert "dir listing here" in msg


def test_format_duplicate_skip_without_replay_uses_fallback():
    task = init_new_task({"id": "T-FB", "title": "x", "description": "d"})
    msg = format_duplicate_skip_output(
        "grep",
        arguments={"pattern": "foo"},
        task=task,
        fallback_summary="pattern=foo",
    )
    assert "[skipped duplicate]" in msg
    assert "grep" in msg
