"""Tests for Ollama retry settings, cooldown burst, and service log resolution."""

from unittest.mock import MagicMock, patch

from backend.agents.scrum_agent import ScrumAgent
from backend.bootstrap import initialize
from backend.services.ollama_service_log import LogSource, read_service_log_snapshot, resolve_log_source
from backend.services.workflow_settings import get_workflow_settings, reset_workflow_settings, save_workflow_settings


def test_default_ollama_timeout_is_300():
    initialize()
    reset_workflow_settings()
    ws = get_workflow_settings()
    assert ws.get("ollamaRequestTimeoutSec") == 300
    assert ws.get("ollamaMaxRetries") == 4
    assert ws.get("ollamaCooldownRetryEnabled") is True


def test_get_client_uses_workflow_timeout():
    initialize()
    reset_workflow_settings()
    save_workflow_settings({"ollamaRequestTimeoutSec": 420})
    agent = ScrumAgent("Developer", "test-model", "system", "http://localhost:11434")
    with patch("backend.agents.scrum_agent.Client") as mock_client:
        agent._get_client()
        mock_client.assert_called_with(host="http://localhost:11434", timeout=420.0)


def test_classify_ollama_error_timeout():
    assert ScrumAgent._classify_ollama_error("HTTPConnectionPool timed out") == "timeout"
    assert ScrumAgent._classify_ollama_error("Connection refused") == "connection"
    assert ScrumAgent._classify_ollama_error("exceed_context_size_error") == "context_overflow"


def test_chat_cooldown_retry_on_transient_failure():
    initialize()
    reset_workflow_settings()
    save_workflow_settings(
        {
            "ollamaMaxRetries": 2,
            "ollamaRetryDelaySec": [0, 0],
            "ollamaCooldownRetryEnabled": True,
            "ollamaCooldownRetrySec": 0,
            "ollamaCooldownRetryAttempts": 1,
        }
    )
    agent = ScrumAgent("Developer", "test-model", "system", "http://localhost:11434")
    mock_client = MagicMock()
    success = MagicMock()
    success.message.content = "ok"
    success.message.tool_calls = None
    mock_client.chat.side_effect = [Exception("connection refused"), success]
    with patch.object(agent, "_get_client", return_value=mock_client):
        with patch("backend.agents.scrum_agent.time.sleep"):
            result = agent._chat([{"role": "user", "content": "hi"}])
    assert result is success
    assert mock_client.chat.call_count == 2


def test_chat_skips_cooldown_on_context_overflow():
    initialize()
    reset_workflow_settings()
    save_workflow_settings(
        {
            "ollamaMaxRetries": 1,
            "ollamaRetryDelaySec": [0],
            "ollamaCooldownRetryEnabled": True,
            "ollamaCooldownRetryAttempts": 2,
        }
    )
    agent = ScrumAgent("Developer", "test-model", "system", "http://localhost:11434")
    mock_client = MagicMock()
    mock_client.chat.side_effect = Exception("exceed_context_size_error")
    with patch.object(agent, "_get_client", return_value=mock_client):
        with patch("backend.agents.scrum_agent.time.sleep"):
            result = agent._chat([{"role": "user", "content": "hi"}])
    assert result is None
    assert mock_client.chat.call_count == 1


def test_save_ollama_timeout_via_api(tmp_path, monkeypatch):
    monkeypatch.setenv("ALLHANDS_HOME", str(tmp_path))
    initialize()
    from fastapi.testclient import TestClient
    from backend.main import app

    client = TestClient(app)
    resp = client.post("/api/workflow/settings", json={"ollamaRequestTimeoutSec": 500})
    assert resp.status_code == 200
    ws = resp.json().get("workflowSettings") or {}
    assert ws.get("ollamaRequestTimeoutSec") == 500


def test_resolve_log_source_ollama_cli_preferred():
    with patch("backend.services.ollama_service_log._which", return_value="/usr/bin/ollama"):
        with patch("backend.services.ollama_service_log._ollama_logs_cli_supported", return_value=True):
            with patch("backend.services.ollama_service_log._platform_file_source", return_value=None):
                source = resolve_log_source()
    assert source.kind == "ollama_cli"


def test_resolve_log_source_windows_file_when_logs_unsupported():
    with patch("backend.services.ollama_service_log._which", return_value=r"C:\Program Files\Ollama\ollama.exe"):
        with patch("backend.services.ollama_service_log._ollama_logs_cli_supported", return_value=False):
            with patch("backend.services.ollama_service_log.platform.system", return_value="Windows"):
                with patch.dict("os.environ", {"LOCALAPPDATA": r"C:\Users\test\AppData\Local"}):
                    with patch("backend.services.ollama_service_log.Path.exists", return_value=True):
                        source = resolve_log_source()
    assert source.kind == "file"
    assert source.path is not None
    assert "server.log" in source.path


def test_read_service_log_falls_back_from_unsupported_cli(tmp_path):
    log_path = tmp_path / "server.log"
    log_path.write_text("line one\nline two\n", encoding="utf-8")
    with patch("backend.services.ollama_service_log.resolve_log_source") as mock_resolve:
        mock_resolve.return_value = LogSource(
            kind="ollama_cli",
            command=["ollama", "logs", "-n", "50"],
            note="ollama logs CLI",
        )
        with patch(
            "backend.services.ollama_service_log._run_command",
            return_value=("", "ollama logs command not supported by this Ollama version"),
        ):
            with patch(
                "backend.services.ollama_service_log._platform_file_source",
                return_value=LogSource(kind="file", path=str(log_path), note="Windows server.log"),
            ):
                snapshot = read_service_log_snapshot(lines=10)
    assert snapshot["source"] == "file"
    assert "line two" in snapshot["text"]
    assert snapshot["error"] is None


def test_resolve_log_source_journalctl_on_linux():
    with patch("backend.services.ollama_service_log._which", side_effect=lambda c: "/bin/journalctl" if c == "journalctl" else None):
        with patch("backend.services.ollama_service_log.platform.system", return_value="Linux"):
            source = resolve_log_source()
    assert source.kind == "journalctl"


def test_resolve_log_source_windows_file():
    with patch("backend.services.ollama_service_log._which", return_value=None):
        with patch("backend.services.ollama_service_log.platform.system", return_value="Windows"):
            with patch.dict("os.environ", {"LOCALAPPDATA": r"C:\Users\test\AppData\Local"}):
                with patch("backend.services.ollama_service_log.Path.exists", return_value=True):
                    source = resolve_log_source()
    assert source.kind == "file"
    assert source.path is not None
    assert "server.log" in source.path
