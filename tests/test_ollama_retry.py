"""Tests for Ollama retry settings, cooldown burst, and service log resolution."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from backend.agents.scrum_agent import ScrumAgent
from backend.bootstrap import initialize
from backend.services.ollama_service_log import LogSource, read_service_log_snapshot, resolve_log_source
from backend.services.workflow_settings import get_workflow_settings, reset_workflow_settings, save_workflow_settings


def test_default_ollama_timeout_is_900():
    initialize()
    reset_workflow_settings()
    ws = get_workflow_settings()
    assert ws.get("ollamaRequestTimeoutSec") == 900
    assert ws.get("ollamaEmptyGenerationTimeoutSec") == 90
    assert ws.get("ollamaMaxRetries") == 4
    assert ws.get("ollamaCooldownRetryEnabled") is True


def test_get_client_uses_workflow_timeout():
    initialize()
    reset_workflow_settings()
    save_workflow_settings({"ollamaRequestTimeoutSec": 420})
    agent = ScrumAgent("Developer", "test-model", "system", "http://localhost:11434")
    with patch("ollama.Client") as mock_client:
        agent._get_client()
        mock_client.assert_called_with(host="http://localhost:11434", timeout=420.0)


def test_classify_ollama_error_timeout():
    assert ScrumAgent._classify_ollama_error("HTTPConnectionPool timed out") == "timeout"
    assert ScrumAgent._classify_ollama_error("Connection refused") == "connection"
    assert ScrumAgent._classify_ollama_error("exceed_context_size_error") == "context_overflow"
    assert (
        ScrumAgent._classify_ollama_error("Ollama empty generation timed out after 90s")
        == "empty_generation_timeout"
    )


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
    mock_provider = MagicMock()
    success = MagicMock()
    success.message.content = "ok"
    success.message.tool_calls = None
    mock_provider.chat.side_effect = [Exception("connection refused"), success]
    with patch.object(agent, "_get_provider", return_value=mock_provider):
        with patch("backend.agents.scrum_agent.time.sleep"):
            result = agent._chat([{"role": "user", "content": "hi"}])
    assert result is success
    assert mock_provider.chat.call_count == 2


def test_chat_does_not_retry_timeout():
    initialize()
    reset_workflow_settings()
    save_workflow_settings(
        {
            "ollamaMaxRetries": 4,
            "ollamaRetryDelaySec": [0, 0, 0, 0],
            "ollamaCooldownRetryEnabled": True,
            "ollamaCooldownRetrySec": 0,
            "ollamaCooldownRetryAttempts": 2,
        }
    )
    agent = ScrumAgent("Developer", "test-model", "system", "http://localhost:11434")
    mock_provider = MagicMock()
    mock_provider.chat.side_effect = Exception("HTTPConnectionPool timed out")
    with patch.object(agent, "_get_provider", return_value=mock_provider):
        with patch("backend.agents.scrum_agent.time.sleep"):
            result = agent._chat([{"role": "user", "content": "hi"}])
    assert result is None
    assert mock_provider.chat.call_count == 1
    assert agent._last_chat_error_type == "timeout"


def test_chat_does_not_retry_empty_generation_timeout():
    initialize()
    reset_workflow_settings()
    save_workflow_settings(
        {
            "ollamaMaxRetries": 4,
            "ollamaRetryDelaySec": [0, 0, 0, 0],
            "ollamaCooldownRetryEnabled": True,
            "ollamaCooldownRetrySec": 0,
            "ollamaCooldownRetryAttempts": 2,
        }
    )
    agent = ScrumAgent("Developer", "test-model", "system", "http://localhost:11434")
    mock_provider = MagicMock()
    mock_provider.chat.side_effect = Exception("Ollama empty generation timed out after 90s")
    with patch.object(agent, "_get_provider", return_value=mock_provider):
        with patch("backend.agents.scrum_agent.time.sleep"):
            result = agent._chat([{"role": "user", "content": "hi"}])
    assert result is None
    assert mock_provider.chat.call_count == 1
    assert agent._last_chat_error_type == "empty_generation_timeout"


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
    mock_provider = MagicMock()
    mock_provider.chat.side_effect = Exception("exceed_context_size_error")
    with patch.object(agent, "_get_provider", return_value=mock_provider):
        with patch("backend.agents.scrum_agent.time.sleep"):
            result = agent._chat([{"role": "user", "content": "hi"}])
    assert result is None
    assert agent._last_chat_error_type == "context_overflow"
    assert mock_provider.chat.call_count >= 1


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


def test_should_stop_after_write_and_dup_verify():
    initialize()
    reset_workflow_settings()
    agent = ScrumAgent("Developer", "test-model", "system", "http://localhost:11434")
    agent._dev_phase_graph = SimpleNamespace(write_succeeded=True)
    call = object()
    dup = SimpleNamespace(duplicate_skip=True)
    results = {id(call): ("run_command", {}, dup, None)}
    assert agent._should_stop_after_write_and_dup_verify(results, [call]) is True
    no_dup = SimpleNamespace(duplicate_skip=False)
    results_no = {id(call): ("run_command", {}, no_dup, None)}
    assert agent._should_stop_after_write_and_dup_verify(results_no, [call]) is False
    agent._dev_phase_graph = SimpleNamespace(write_succeeded=False)
    with patch("backend.services.step_diagnostics.get_active_trace", return_value=None):
        assert agent._should_stop_after_write_and_dup_verify(results, [call]) is False
    read_call = object()
    read_results = {id(read_call): ("read_file", {}, dup, None)}
    agent._dev_phase_graph = SimpleNamespace(write_succeeded=True)
    assert agent._should_stop_after_write_and_dup_verify(read_results, [read_call]) is False


def test_should_stop_after_write_when_verify_already_in_success_keys():
    import json

    initialize()
    reset_workflow_settings()
    agent = ScrumAgent("Developer", "test-model", "system", "http://localhost:11434")
    agent._dev_phase_graph = SimpleNamespace(write_succeeded=True)
    write_call = object()
    write_res = SimpleNamespace(success=True, duplicate_skip=False)
    results = {id(write_call): ("write_file", {"path": "a.dart"}, write_res, None)}
    keys = [
        (
            "run_command",
            json.dumps({"command": "flutter test test/data/store_repository_test.dart"}),
        )
    ]
    assert (
        agent._dup_verify_stop_source(
            results, [write_call], successful_tool_keys=keys
        )
        == "seeded_keys"
    )
    assert (
        agent._should_stop_after_write_and_dup_verify(
            results, [write_call], successful_tool_keys=keys
        )
        is True
    )
    assert (
        agent._should_stop_after_write_and_dup_verify(
            results, [write_call], successful_tool_keys=[]
        )
        is False
    )


def test_consume_chat_stream_folds_chunks():
    from backend.services.llm_provider import ChatResult, ProviderMessage, consume_chat_stream

    chunks = [
        ChatResult(message=ProviderMessage(content="Hel"), prompt_eval_count=10, eval_count=1),
        ChatResult(message=ProviderMessage(content="lo"), prompt_eval_count=10, eval_count=2),
    ]
    merged = consume_chat_stream(iter(chunks), empty_timeout_sec=2)
    assert merged.message.content == "Hello"
    assert merged.eval_count == 2
    assert merged.prompt_eval_count == 10


def test_consume_chat_stream_times_out_on_silent_iterator():
    import time

    from backend.services.llm_provider import EmptyGenerationTimeout, consume_chat_stream

    def silent():
        time.sleep(2)
        yield from ()

    try:
        consume_chat_stream(silent(), empty_timeout_sec=0.2)
        raise AssertionError("expected EmptyGenerationTimeout")
    except EmptyGenerationTimeout:
        pass


def test_consume_chat_stream_wall_clock_ignores_empty_heartbeats():
    import time

    from backend.services.llm_provider import (
        ChatResult,
        EmptyGenerationTimeout,
        ProviderMessage,
        consume_chat_stream,
    )

    def heartbeats():
        for _ in range(20):
            time.sleep(0.05)
            yield ChatResult(message=ProviderMessage(content=None), prompt_eval_count=10, eval_count=0)

    started = time.monotonic()
    try:
        consume_chat_stream(heartbeats(), empty_timeout_sec=0.2)
        raise AssertionError("expected EmptyGenerationTimeout")
    except EmptyGenerationTimeout:
        pass
    elapsed = time.monotonic() - started
    assert elapsed < 0.8


def test_close_chat_stream_calls_close():
    from backend.services.llm_provider import close_chat_stream

    class Box:
        closed = False

        def close(self):
            self.closed = True

    box = Box()
    close_chat_stream(box)
    assert box.closed is True
