"""Cursor-style context rewind: cut recent turns, keep earlier good context."""

from backend.services.llm_context import (
    maybe_rewind_after_failed_writes,
    rewind_recent_turns,
)
from backend.services.workflow_settings import reset_workflow_settings, save_workflow_settings
from backend.storage.project_storage import ProjectStorage


def test_rewind_recent_turns_keeps_head_and_earlier_turn():
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "task"},
        {"role": "assistant", "content": "good plan", "tool_calls": []},
        {"role": "tool", "tool_name": "read_file", "content": "file ok"},
        {"role": "assistant", "content": "", "tool_calls": [{"name": "apply_patch"}]},
        {
            "role": "tool",
            "tool_name": "apply_patch",
            "content": "Error: old_text not found in file",
        },
        {"role": "system", "content": "=== OBSERVATION === patch failed"},
    ]
    removed = rewind_recent_turns(messages, turns=1)
    assert removed >= 2
    assert messages[0]["role"] == "system"
    assert messages[1]["role"] == "user"
    assert any(m.get("tool_name") == "read_file" for m in messages)
    assert not any(m.get("tool_name") == "apply_patch" for m in messages)
    assert any("rewound" in str(m.get("content", "")).lower() for m in messages)


def test_maybe_rewind_after_failed_writes_respects_setting():
    reset_workflow_settings()
    save_workflow_settings({"enableContextRewind": False})
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "task"},
        {"role": "assistant", "content": "x"},
        {"role": "tool", "tool_name": "apply_patch", "content": "fail"},
    ]
    assert maybe_rewind_after_failed_writes(
        messages, write_attempted=True, write_succeeded=False
    ) == 0
    assert len(messages) == 4

    save_workflow_settings({"enableContextRewind": True, "contextRewindTurns": 1})
    removed = maybe_rewind_after_failed_writes(
        messages, write_attempted=True, write_succeeded=False
    )
    assert removed > 0
    assert maybe_rewind_after_failed_writes(
        messages, write_attempted=True, write_succeeded=True
    ) == 0


def test_storage_rewind_chat_drops_last_turn(tmp_path):
    db = tmp_path / "rewind.db"
    store = ProjectStorage(str(db))
    pid = "proj-rewind"
    store.save_chat_message(pid, "user", "first question")
    store.save_chat_message(pid, "assistant", "first answer", agent="Developer")
    store.save_chat_message(pid, "user", "second question")
    store.save_chat_message(pid, "assistant", "Stopped: explore tool budget", agent="Developer")

    result = store.rewind_chat_messages(pid, drop_turns=1, mode="turns")
    assert result["deleted"] == 2
    kept = result["chatMessages"]
    assert len(kept) == 2
    assert kept[0]["content"] == "first question"
    assert kept[1]["content"] == "first answer"


def test_storage_rewind_before_last_error(tmp_path):
    db = tmp_path / "rewind2.db"
    store = ProjectStorage(str(db))
    pid = "proj-err"
    store.save_chat_message(pid, "user", "ok turn")
    store.save_chat_message(pid, "assistant", "all good")
    store.save_chat_message(pid, "user", "broken turn")
    store.save_chat_message(pid, "assistant", "Error: something failed badly")

    result = store.rewind_chat_messages(pid, mode="before_last_error")
    assert result["deleted"] == 2
    assert [m["content"] for m in result["chatMessages"]] == ["ok turn", "all good"]
