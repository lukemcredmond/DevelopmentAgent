"""Tests for Ollama retry settings, cooldown burst, and service log resolution."""

from unittest.mock import MagicMock, patch

from backend.agents.scrum_agent import ScrumAgent
from backend.bootstrap import initialize
from backend.services.ollama_service_log import LogSource, resolve_log_source
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


def test_resolve_log_source_ollama_cli_preferred():
    with patch("backend.services.ollama_service_log._which", return_value="/usr/bin/ollama"):
        source = resolve_log_source()
    assert source.kind == "ollama_cli"


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
