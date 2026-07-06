"""Tests for Cursor-class tool execution gap closures."""

import os
import tempfile

from fastapi.testclient import TestClient

from backend.bootstrap import initialize
from backend.main import app
from backend.services.command_policy import (
    run_command_requires_approval,
    split_chained_commands,
    validate_command,
)
from backend.services.parallel_tools import is_parallel_safe, partition_tool_calls
from backend.services.workflow_settings import reset_workflow_settings, save_workflow_settings
from backend.services import mcp_tools


class _FakeCall:
    def __init__(self, name: str):
        self.function = type("Fn", (), {"name": name, "arguments": "{}"})()


def test_parallel_safe_tools_partition():
    calls = [
        _FakeCall("grep"),
        _FakeCall("write_file"),
        _FakeCall("read_file"),
        _FakeCall("list_dir"),
    ]
    parallel, sequential = partition_tool_calls(calls)
    assert len(parallel) == 3
    assert len(sequential) == 1
    assert sequential[0].function.name == "write_file"
    assert is_parallel_safe("grep")
    assert not is_parallel_safe("apply_patch")


def test_command_validate_blocks_chaining_by_default():
    reset_workflow_settings()
    ok, reason = validate_command("npm test && npm run lint")
    assert not ok
    assert "Chained" in reason or "chained" in reason.lower()


def test_command_chaining_when_enabled():
    reset_workflow_settings()
    save_workflow_settings({"allowChainedCommands": True})
    ok, _ = validate_command("npm test && npm run lint")
    assert ok
    parts = split_chained_commands("npm test && npm run lint")
    assert parts == ["npm test", "npm run lint"]


def test_command_allowlist_auto_run():
    reset_workflow_settings()
    save_workflow_settings(
        {
            "requireToolApproval": True,
            "commandAutoRunMode": "allowlist",
            "commandAllowlist": ["pytest", "flutter analyze"],
        }
    )
    assert not run_command_requires_approval("pytest tests/")
    assert run_command_requires_approval("rm -rf node_modules")


def test_command_denylist_auto_run():
    reset_workflow_settings()
    save_workflow_settings(
        {
            "requireToolApproval": True,
            "commandAutoRunMode": "denylist",
            "commandDenylist": ["rm ", "del "],
        }
    )
    assert run_command_requires_approval("rm -rf tmp")
    assert not run_command_requires_approval("pytest")


def test_read_file_line_range_and_list_dir():
    from backend import state
    from backend.workspace.files import list_workspace_dir, read_workspace_file

    initialize()
    with tempfile.TemporaryDirectory() as tmp:
        state.WORKSPACE_DIR = tmp
        sample = os.path.join(tmp, "sample.txt")
        with open(sample, "w", encoding="utf-8") as fh:
            fh.write("line1\nline2\nline3\n")

        full = read_workspace_file("sample.txt")
        assert "line1" in full

        slice_text = read_workspace_file("sample.txt", start_line=2, end_line=2)
        assert "line2" in slice_text
        assert "line1" not in slice_text.split("line2")[0][-10:]

        listing = list_workspace_dir(".")
        assert "sample.txt" in listing
        assert "file" in listing


def test_non_blocking_tool_approval():
    from backend.services.tool_approval import request_tool_approval, resolve_tool_approval

    reset_workflow_settings()
    save_workflow_settings({"requireToolApproval": True, "nonBlockingToolApproval": True})

    approved, msg, approval_id = request_tool_approval(
        "run-1",
        "write_file",
        {"path": "x.txt", "content": "hi"},
        task_id="T1",
        agent_id="dev",
    )
    assert not approved
    assert approval_id
    assert msg.startswith("AWAITING_APPROVAL:")
    assert resolve_tool_approval(approval_id, False)


def test_mcp_tool_enabled_filter():
    spec = {
        "enabledTools": ["search", "fetch"],
        "disabledTools": ["delete"],
    }
    assert mcp_tools._tool_enabled("search", spec)
    assert not mcp_tools._tool_enabled("delete", spec)
    assert not mcp_tools._tool_enabled("other", spec)


def test_background_terminal_api():
    initialize()
    reset_workflow_settings()
    client = TestClient(app)

    bad = client.post("/api/terminal/background", json={"command": "cd /tmp && ls"})
    assert bad.status_code == 400

    save_workflow_settings({"allowChainedCommands": True})
    ok = client.post("/api/terminal/background", json={"command": "echo hello-bg"})
    assert ok.status_code == 200
    session_id = ok.json()["sessionId"]

    listed = client.get("/api/terminal/background")
    assert listed.status_code == 200
    ids = [s["id"] for s in listed.json()["sessions"]]
    assert session_id in ids

    output = client.get(f"/api/terminal/background/{session_id}")
    assert output.status_code == 200
    assert "sessionId" in output.json()

    stopped = client.delete(f"/api/terminal/background/{session_id}")
    assert stopped.status_code == 200


def test_tool_approval_endpoint_returns_state():
    from backend.services.tool_approval import queue_tool_approval

    initialize()
    reset_workflow_settings()
    client = TestClient(app)

    approval = queue_tool_approval(
        "run-2",
        "run_command",
        {"command": "echo test"},
        task_id="T2",
        agent_id="dev",
    )
    resp = client.post(f"/api/tools/approvals/{approval.id}", json={"approved": False})
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("ok") is True
    assert "board" in body
