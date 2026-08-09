"""Tests for post-fail apply_patch recovery helpers."""

from types import SimpleNamespace

from backend.services.llm_context import rewind_recent_turns
from backend.services.patch_recovery import (
    PATCH_RECOVERY_MARKER,
    PATCH_RECOVERY_REMINDER_MARKER,
    apply_batch_to_recovery_state,
    build_patch_recovery_nudge,
    build_patch_recovery_reminder,
    failed_apply_patch_paths,
    paths_needing_read_before_patch,
)


def _res(ok: bool):
    return SimpleNamespace(success=ok)


def test_failed_apply_patch_paths_and_nudge():
    batch = [
        ("read_file", {"path": "a.dart"}, _res(True)),
        ("apply_patch", {"path": "lib/foo.dart"}, _res(False)),
        ("apply_patch", {"path": "lib/foo.dart"}, _res(False)),
    ]
    paths = failed_apply_patch_paths(batch)
    assert paths == ["lib/foo.dart"]
    msg = build_patch_recovery_nudge(paths)
    assert PATCH_RECOVERY_MARKER in msg
    assert "lib/foo.dart" in msg
    assert "read_file" in msg


def test_paths_needing_read_before_patch():
    pending = {"lib/foo.dart"}
    read_ok: set = set()
    batch = [
        ("apply_patch", {"path": "lib/foo.dart"}, _res(False)),
    ]
    assert paths_needing_read_before_patch(pending, read_ok, batch) == ["lib/foo.dart"]

    batch_ok = [
        ("read_file", {"path": "lib/foo.dart"}, _res(True)),
        ("apply_patch", {"path": "lib/foo.dart"}, _res(True)),
    ]
    assert paths_needing_read_before_patch(pending, read_ok, batch_ok) == []


def test_apply_batch_updates_recovery_state():
    pending = {"lib/foo.dart"}
    read_ok: set = set()
    pending, read_ok = apply_batch_to_recovery_state(
        pending,
        read_ok,
        [("read_file", {"path": "lib/foo.dart"}, _res(True))],
    )
    assert "lib/foo.dart" in pending
    assert "lib/foo.dart" in read_ok

    pending, read_ok = apply_batch_to_recovery_state(
        pending,
        read_ok,
        [("apply_patch", {"path": "lib/foo.dart"}, _res(True))],
    )
    assert "lib/foo.dart" not in pending
    assert "lib/foo.dart" not in read_ok


def test_rewind_then_recovery_nudge_survives():
    """Simulate rewind removing failed turn, then durable recovery message remains."""
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "task"},
        {"role": "assistant", "content": "", "tool_calls": []},
        {
            "role": "tool",
            "tool_name": "apply_patch",
            "content": "Error: old_text not found",
        },
        {
            "role": "system",
            "content": "apply_patch failed — call read_file (will be rewound away)",
        },
    ]
    rewind_recent_turns(messages, turns=1)
    # Soft hint was removed with the turn; inject durable recovery after rewind
    paths = ["lib/widget.dart"]
    messages.append({"role": "system", "content": build_patch_recovery_nudge(paths)})
    assert any(PATCH_RECOVERY_MARKER in str(m.get("content", "")) for m in messages)
    assert any("lib/widget.dart" in str(m.get("content", "")) for m in messages)
    # Rewind note may remain; soft hint from failed turn should be gone
    assert not any("will be rewound away" in str(m.get("content", "")) for m in messages)


def test_reminder_message():
    msg = build_patch_recovery_reminder(["a.dart"])
    assert PATCH_RECOVERY_REMINDER_MARKER in msg
    assert "a.dart" in msg
