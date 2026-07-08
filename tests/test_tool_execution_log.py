"""Tool execution log approval and history tests."""

from unittest.mock import patch

from backend.bootstrap import initialize


@patch("backend.services.tool_execution_service.request_tool_approval")
@patch("backend.services.tool_execution_service.tool_requires_approval", return_value=True)
def test_pending_approval_emits_tool_end(mock_requires, mock_approval, tmp_path, monkeypatch):
    initialize()
    from backend import state
    from backend.services.tool_execution_service import execute_tool, get_tool_history

    state.TOOL_EXECUTION_LOG.clear()
    monkeypatch.setattr(state, "WORKSPACE_DIR", str(tmp_path))
    (tmp_path / "x.txt").write_text("hi", encoding="utf-8")

    mock_approval.return_value = (False, "AWAITING_APPROVAL: user must approve", "appr-1")

    result = execute_tool(
        "dev",
        "write_file",
        {"path": "x.txt", "content": "changed"},
        task_id="T-APPR",
        source="agent",
        skip_approval=False,
    )

    assert result.pending_approval is True
    history = get_tool_history(limit=10)
    assert len(history) >= 1
    assert history[0]["status"] == "awaiting_approval"
    assert history[0]["toolName"] == "write_file"


def test_agent_tool_logged_to_history(tmp_path, monkeypatch):
    initialize()
    from backend import state
    from backend.services.tool_execution_service import execute_tool, get_tool_history

    state.TOOL_EXECUTION_LOG.clear()
    monkeypatch.setattr(state, "WORKSPACE_DIR", str(tmp_path))
    (tmp_path / "readme.txt").write_text("hello", encoding="utf-8")

    result = execute_tool(
        "dev",
        "read_file",
        {"path": "readme.txt"},
        task_id="T-SPIKE",
        source="agent",
        skip_approval=True,
    )

    assert result.success is True
    history = get_tool_history(limit=5)
    assert any(ev.get("toolName") == "read_file" and ev.get("taskId") == "T-SPIKE" for ev in history)
