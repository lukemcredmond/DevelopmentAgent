"""Hardened apply_patch: strip N| prefixes and whitespace-tolerant match."""

from backend import state
from backend.bootstrap import initialize
from backend.workspace.files import (
    apply_workspace_patch,
    record_step_file_read,
    write_workspace_file,
)


def test_apply_patch_strips_numbered_line_prefixes():
    initialize()
    state.ACTIVE_SPRINT_TASK_ID = "T-NUM"
    state.STEP_FILE_READS.clear()
    write_workspace_file("num_patch.txt", "alpha\nbeta\ngamma\n")
    record_step_file_read(
        "num_patch.txt",
        "File: num_patch.txt (lines 1-3 of 3)\n1|alpha\n2|beta\n3|gamma\n",
    )
    result = apply_workspace_patch(
        "num_patch.txt",
        "2|beta",
        "2|BETA",
    )
    assert "Successfully saved" in result
    assert state.VIRTUAL_FILESYSTEM["num_patch.txt"] == "alpha\nBETA\ngamma\n"


def test_apply_patch_tolerant_trailing_whitespace():
    initialize()
    state.ACTIVE_SPRINT_TASK_ID = "T-WS"
    state.STEP_FILE_READS.clear()
    write_workspace_file("ws_patch.txt", "hello world  \nok\n")
    record_step_file_read("ws_patch.txt", "hello world  \nok\n")
    result = apply_workspace_patch(
        "ws_patch.txt",
        "hello world",
        "hello there",
    )
    assert "Successfully saved" in result
    assert "hello there" in state.VIRTUAL_FILESYSTEM["ws_patch.txt"]


def test_apply_patch_rejects_old_text_not_in_last_read():
    initialize()
    state.ACTIVE_SPRINT_TASK_ID = "T-VAL"
    state.STEP_FILE_READS.clear()
    write_workspace_file("val_patch.txt", "real content here\n")
    record_step_file_read("val_patch.txt", "real content here\n")
    result = apply_workspace_patch(
        "val_patch.txt",
        "invented stale snippet",
        "replacement",
    )
    assert "not in last read_file" in result
