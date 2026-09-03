"""LLM provider mapping, health, embed split, and Ollama option regression."""

from __future__ import annotations

import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

from backend.bootstrap import initialize
from backend.services.model_test_runner import (
    MAX_HEALTH_TIMEOUT_SEC,
    build_test_provider,
    build_test_slots,
    probe_model,
    reset_job_state,
    run_agent_model_tests,
)
from backend.services.llm_provider import (
    ChatResult,
    DEFAULT_LMSTUDIO_URL,
    DEFAULT_OLLAMA_URL,
    HealthResult,
    OllamaProvider,
    OpenAICompatProvider,
    ProviderMessage,
    chat_config,
    chat_result_from_openai,
    embed_config,
    get_embed_provider,
    infer_provider_from_url,
    to_openai_messages,
)
from backend.services.workflow_settings import reset_workflow_settings, save_workflow_settings


def test_infer_provider_from_lmstudio_url():
    assert infer_provider_from_url("http://localhost:1234/v1") == "openai_compat"
    assert infer_provider_from_url("http://localhost:1234") == "openai_compat"
    assert infer_provider_from_url("http://localhost:11434") == "ollama"


def test_to_openai_messages_maps_tool_call_id():
    messages = [
        {"role": "user", "content": "hi"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call_abc",
                    "function": {"name": "read_file", "arguments": {"path": "a.py"}},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call_abc", "content": "ok"},
    ]
    out = to_openai_messages(messages)
    assert out[1]["tool_calls"][0]["id"] == "call_abc"
    assert out[1]["tool_calls"][0]["function"]["arguments"] == '{"path": "a.py"}'
    assert out[2]["tool_call_id"] == "call_abc"


def test_chat_result_from_openai_usage_and_tools():
    payload = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {"name": "list_files", "arguments": "{}"},
                        }
                    ],
                }
            }
        ],
        "usage": {"prompt_tokens": 11, "completion_tokens": 4},
    }
    result = chat_result_from_openai(payload)
    assert result.prompt_eval_count == 11
    assert result.eval_count == 4
    assert result.message.tool_calls[0].id == "call_1"
    assert result.message.tool_calls[0].function.name == "list_files"


def test_openai_compat_list_models_from_v1():
    provider = OpenAICompatProvider("http://localhost:1234/v1")
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {"data": [{"id": "local-model"}]}
    with patch("backend.services.llm_provider.requests.get", return_value=response) as mock_get:
        health = provider.health()
    mock_get.assert_called_once()
    assert mock_get.call_args.args[0] == "http://localhost:1234/v1/models"
    assert health.ok is True
    assert health.models == ["local-model"]
    assert health.provider == "openai_compat"


def test_ollama_chat_sends_num_ctx_and_keep_alive():
    initialize()
    reset_workflow_settings()
    from backend.services.llm_provider import OllamaProvider

    provider = OllamaProvider("http://localhost:11434")
    client = MagicMock()
    client.chat.return_value = MagicMock(message=MagicMock(content="ok", tool_calls=None), prompt_eval_count=1, eval_count=1)
    with patch.object(provider, "_get_client", return_value=client):
        provider.chat(
            "qwen",
            [{"role": "user", "content": "hi"}],
            options={"temperature": 0.1, "num_ctx": 8192, "keep_alive": "30m"},
        )
    kwargs = client.chat.call_args.kwargs
    assert kwargs["options"]["num_ctx"] == 8192
    assert kwargs["keep_alive"] == "30m"


def _openai_chat_response(content: str = "hi"):
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {
        "choices": [{"message": {"role": "assistant", "content": content, "tool_calls": []}}],
        "usage": {"prompt_tokens": 2, "completion_tokens": 1},
    }
    return response


def _native_models_response(models=None, status_code: int = 200):
    response = MagicMock()
    response.status_code = status_code
    response.json.return_value = {"models": list(models or [])}
    response.text = "not found" if status_code == 404 else ""
    return response


def _native_load_response(context_length: int = 8192, status_code: int = 200, error_text: str = ""):
    response = MagicMock()
    response.status_code = status_code
    response.text = error_text or ""
    response.json.return_value = {
        "load_config": {"context_length": context_length},
        "load_time_seconds": 1.2,
    }
    return response


def _lmstudio_http(native_models=None, load=None, chat=None):
    listed = _native_models_response(native_models)

    def handle_get(url, **_kwargs):
        if "/api/v1/models" in url and not url.rstrip("/").endswith("/load"):
            return listed
        return listed

    def handle_post(url, **kwargs):
        if url.endswith("/api/v1/models/load"):
            return load if load is not None else _native_load_response()
        if url.endswith("/chat/completions"):
            return chat if chat is not None else _openai_chat_response()
        unload = MagicMock()
        unload.status_code = 200
        return unload

    return handle_get, handle_post


def test_openai_compat_chat_omits_num_ctx():
    provider = OpenAICompatProvider("http://localhost:1234/v1")
    handle_get, handle_post = _lmstudio_http(load=_native_load_response(32768))
    with patch("backend.services.llm_provider.requests.get", side_effect=handle_get):
        with patch("backend.services.llm_provider.requests.post", side_effect=handle_post) as mock_post:
            provider.chat(
                "local",
                [{"role": "user", "content": "hi"}],
                options={"temperature": 0.1, "num_ctx": 32768, "keep_alive": "30m"},
            )
    completions = [
        call for call in mock_post.call_args_list if str(call.args[0]).endswith("/chat/completions")
    ]
    assert completions
    payload = completions[0].kwargs["json"]
    assert "num_ctx" not in payload
    assert payload["messages"][0]["content"] == "hi"


def test_openai_compat_chat_loads_then_completes():
    provider = OpenAICompatProvider("http://localhost:1234/v1")
    handle_get, handle_post = _lmstudio_http(load=_native_load_response(8192))
    with patch("backend.services.llm_provider.requests.get", side_effect=handle_get):
        with patch("backend.services.llm_provider.requests.post", side_effect=handle_post) as mock_post:
            provider.chat(
                "qwen/qwen3-27b",
                [{"role": "user", "content": "hi"}],
                options={"num_ctx": 8192},
            )
            urls = [str(call.args[0]) for call in mock_post.call_args_list]
            load_calls = [call for call in mock_post.call_args_list if str(call.args[0]).endswith("/api/v1/models/load")]
            assert load_calls
            assert load_calls[0].kwargs["json"]["context_length"] == 8192
            assert load_calls[0].kwargs["json"]["model"] == "qwen/qwen3-27b"
            assert any(url.endswith("/chat/completions") for url in urls)
            mock_post.reset_mock()
            provider.chat(
                "qwen/qwen3-27b",
                [{"role": "user", "content": "again"}],
                options={"num_ctx": 8192},
            )
            reload_calls = [
                call for call in mock_post.call_args_list if str(call.args[0]).endswith("/api/v1/models/load")
            ]
            assert reload_calls == []
            assert any(str(call.args[0]).endswith("/chat/completions") for call in mock_post.call_args_list)


def test_openai_compat_chat_retries_smaller_context_on_load_refuse():
    provider = OpenAICompatProvider("http://localhost:1234/v1")
    listed = _native_models_response([])
    refused = _native_load_response(status_code=400, error_text="insufficient system resources")
    loaded = _native_load_response(4096)
    chat = _openai_chat_response()
    contexts = []

    def handle_post(url, **kwargs):
        if url.endswith("/api/v1/models/load"):
            ctx = kwargs["json"]["context_length"]
            contexts.append(ctx)
            return loaded if ctx <= 4096 else refused
        if url.endswith("/chat/completions"):
            return chat
        unload = MagicMock()
        unload.status_code = 200
        return unload

    with patch("backend.services.llm_provider.requests.get", return_value=listed):
        with patch("backend.services.llm_provider.requests.post", side_effect=handle_post):
            provider.chat(
                "qwen/qwen3-27b",
                [{"role": "user", "content": "hi"}],
                options={"num_ctx": 16384},
            )
    assert contexts[0] == 16384
    assert 4096 in contexts
    assert contexts[-1] == 4096


def test_openai_compat_chat_skips_load_when_already_on_server():
    provider = OpenAICompatProvider("http://localhost:1234/v1")
    listed = _native_models_response(
        [
            {
                "key": "local-model",
                "loaded_instances": [
                    {"id": "local-model", "config": {"context_length": 8192}},
                ],
            }
        ]
    )
    with patch("backend.services.llm_provider.requests.get", return_value=listed):
        with patch("backend.services.llm_provider.requests.post", return_value=_openai_chat_response()) as mock_post:
            provider.chat("local-model", [{"role": "user", "content": "hi"}], options={"num_ctx": 8192})
    urls = [str(call.args[0]) for call in mock_post.call_args_list]
    assert not any(url.endswith("/api/v1/models/load") for url in urls)
    assert any(url.endswith("/chat/completions") for url in urls)


def test_ollama_chat_does_not_call_lmstudio_load():
    from backend.services.llm_provider import OllamaProvider

    provider = OllamaProvider("http://localhost:11434")
    client = MagicMock()
    client.chat.return_value = MagicMock(
        message=MagicMock(content="ok", tool_calls=None), prompt_eval_count=1, eval_count=1
    )
    with patch.object(provider, "_get_client", return_value=client):
        with patch("backend.services.llm_provider.requests.post") as mock_post:
            provider.chat(
                "qwen",
                [{"role": "user", "content": "hi"}],
                options={"temperature": 0.1, "num_ctx": 8192, "keep_alive": "30m"},
            )
    mock_post.assert_not_called()
    assert client.chat.called


def test_health_endpoint_uses_v1_models(monkeypatch):
    initialize()
    reset_workflow_settings()
    save_workflow_settings(
        {
            "llmProvider": "openai_compat",
            "llmProviderPreset": "lmstudio",
            "llmBaseUrl": DEFAULT_LMSTUDIO_URL,
        }
    )
    from fastapi.testclient import TestClient
    from backend.main import app

    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {"data": [{"id": "loaded-model"}]}
    with patch("backend.services.llm_provider.requests.get", return_value=response):
        client = TestClient(app)
        data = client.get("/api/ollama/health?url=http://localhost:1234/v1").json()
    assert data["ok"] is True
    assert data["provider"] == "openai_compat"
    assert data["models"] == ["loaded-model"]


def test_legacy_ollama_url_default_does_not_override_lmstudio():
    """Sprint/chat payloads default ollama_url to 11434; that must not hijack LM Studio."""
    initialize()
    reset_workflow_settings()
    save_workflow_settings(
        {
            "llmProvider": "openai_compat",
            "llmProviderPreset": "lmstudio",
            "llmBaseUrl": DEFAULT_LMSTUDIO_URL,
        }
    )

    for legacy in (DEFAULT_OLLAMA_URL, "http://127.0.0.1:11434", "http://localhost:11434/"):
        cfg = chat_config(override_url=legacy)
        assert cfg["provider"] == "openai_compat"
        assert cfg["baseUrl"] == DEFAULT_LMSTUDIO_URL

    # A deliberate OpenAI-compatible override is still honoured.
    remote = chat_config(override_url="http://192.168.1.9:1234/v1")
    assert remote["baseUrl"] == "http://192.168.1.9:1234/v1"


def test_ollama_settings_still_honour_url_override():
    initialize()
    reset_workflow_settings()
    save_workflow_settings(
        {
            "llmProvider": "ollama",
            "llmProviderPreset": "ollama",
            "llmBaseUrl": DEFAULT_OLLAMA_URL,
        }
    )
    cfg = chat_config(override_url="http://10.0.0.5:11434")
    assert cfg["provider"] == "ollama"
    assert cfg["baseUrl"] == "http://10.0.0.5:11434"


def test_embed_provider_stays_on_ollama_when_chat_is_lmstudio():
    initialize()
    reset_workflow_settings()
    save_workflow_settings(
        {
            "llmProvider": "openai_compat",
            "llmProviderPreset": "lmstudio",
            "llmBaseUrl": DEFAULT_LMSTUDIO_URL,
            "embedProvider": "ollama",
            "embedBaseUrl": DEFAULT_OLLAMA_URL,
        }
    )
    chat = chat_config()
    embed = embed_config()
    assert chat["provider"] == "openai_compat"
    assert chat["baseUrl"] == DEFAULT_LMSTUDIO_URL
    assert embed["provider"] == "ollama"
    assert embed["baseUrl"] == DEFAULT_OLLAMA_URL

    provider = get_embed_provider()
    captured = {}

    def fake_post(url, json=None, timeout=None, headers=None):
        captured["url"] = url
        captured["json"] = json
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"embedding": [0.1] * 64}
        return resp

    with patch("backend.services.llm_provider.requests.post", side_effect=fake_post):
        vec = provider.embed("nomic-embed-text", "hello")
    assert captured["url"] == "http://localhost:11434/api/embeddings"
    assert vec is not None
    assert len(vec) == 64


def _test_client():
    from fastapi.testclient import TestClient
    from backend.main import app

    initialize()
    return TestClient(app)


def test_model_endpoint_generates_with_exact_listed_model():
    provider = MagicMock()
    provider.health.return_value = HealthResult(
        ok=True,
        url=DEFAULT_LMSTUDIO_URL,
        models=["loaded-model"],
        provider="openai_compat",
    )
    provider.chat.return_value = ChatResult(message=ProviderMessage(content="OK"))
    with patch("backend.api.ollama.build_test_provider", return_value=provider):
        data = _test_client().post(
            "/api/llm/test-model",
            json={"url": DEFAULT_LMSTUDIO_URL, "model": "loaded-model"},
        ).json()

    assert data["ok"] is True
    assert data["model"] == "loaded-model"
    assert data["provider"] == "openai_compat"
    assert data["response"] == "OK"
    provider.chat.assert_called_once()


def test_model_endpoint_rejects_unknown_model_without_generation():
    provider = MagicMock()
    provider.health.return_value = HealthResult(
        ok=True,
        url=DEFAULT_LMSTUDIO_URL,
        models=["actual-model"],
        provider="openai_compat",
    )
    with patch("backend.api.ollama.build_test_provider", return_value=provider):
        data = _test_client().post(
            "/api/llm/test-model",
            json={"url": DEFAULT_LMSTUDIO_URL, "model": "wrong-model"},
        ).json()

    assert data["ok"] is False
    assert data["errorType"] == "model"
    assert data["models"] == ["actual-model"]
    provider.chat.assert_not_called()
    # Re-probed once in case the first model list was stale.
    assert provider.health.call_count == 2


def test_model_endpoint_reports_unreachable_provider():
    provider = MagicMock()
    provider.health.return_value = HealthResult(
        ok=False,
        url=DEFAULT_LMSTUDIO_URL,
        error="connection refused",
        provider="openai_compat",
    )
    with patch("backend.api.ollama.build_test_provider", return_value=provider):
        data = _test_client().post(
            "/api/llm/test-model",
            json={"url": DEFAULT_LMSTUDIO_URL, "model": "loaded-model"},
        ).json()

    assert data["ok"] is False
    assert data["errorType"] == "connection"
    assert "connection refused" in data["error"]


def test_model_endpoint_reports_generation_failure():
    provider = MagicMock()
    provider.health.return_value = HealthResult(
        ok=True,
        url=DEFAULT_LMSTUDIO_URL,
        models=["loaded-model"],
        provider="openai_compat",
    )
    provider.chat.side_effect = RuntimeError("model failed to load")
    with patch("backend.api.ollama.build_test_provider", return_value=provider):
        data = _test_client().post(
            "/api/llm/test-model",
            json={"url": DEFAULT_LMSTUDIO_URL, "model": "loaded-model"},
        ).json()

    assert data["ok"] is False
    assert data["errorType"] == "generation"
    assert "failed to load" in data["error"]


def test_build_test_slots_skips_blank_and_duplicate_backups():
    slots = build_test_slots(
        {"po": "shared-model", "dev": "dev-model", "cr": "shared-model", "qa": "qa-model"},
        {"po": "", "dev": "shared-model", "cr": "shared-model", "qa": "qa-backup"},
    )
    pairs = [(slot["agentId"], slot["slot"]) for slot in slots]

    # Blank PO backup and CR backup equal to its own primary are both skipped.
    assert ("po", "backup") not in pairs
    assert ("cr", "backup") not in pairs
    assert ("dev", "backup") in pairs
    assert ("qa", "backup") in pairs
    assert len(slots) == 6


def test_run_agent_model_tests_loads_each_unique_model_once():
    from backend import state

    initialize()
    state.PRIMARY_MODELS = {
        "po": "shared-model",
        "dev": "dev-model",
        "cr": "shared-model",
        "qa": "qa-model",
    }
    state.BACKUP_MODELS = {
        "po": "",
        "dev": "shared-model",
        "cr": "shared-model",
        "qa": "qa-backup",
    }
    original_primary = dict(state.PRIMARY_MODELS)
    original_backup = dict(state.BACKUP_MODELS)
    provider = MagicMock()
    provider.health.return_value = HealthResult(
        ok=True,
        url=DEFAULT_LMSTUDIO_URL,
        models=["shared-model", "dev-model", "qa-model", "qa-backup"],
        provider="openai_compat",
    )
    provider.chat.return_value = ChatResult(message=ProviderMessage(content="OK"))

    slots = build_test_slots(state.PRIMARY_MODELS, state.BACKUP_MODELS)
    summary = run_agent_model_tests(slots, provider)

    assert summary["ok"] is True
    assert summary["uniqueModelsTested"] == 4
    assert len(summary["results"]) == 6
    # shared-model covers 3 slots but is loaded only once.
    assert provider.chat.call_count == 4
    assert state.PRIMARY_MODELS == original_primary
    assert state.BACKUP_MODELS == original_backup


def test_run_agent_model_tests_reports_connection_failure_for_each_slot():
    provider = MagicMock()
    provider.health.return_value = HealthResult(
        ok=False,
        url=DEFAULT_LMSTUDIO_URL,
        error="connection refused",
        provider="openai_compat",
    )

    slots = build_test_slots({key: f"{key}-model" for key in ("po", "dev", "cr", "qa")}, {})
    summary = run_agent_model_tests(slots, provider)

    assert summary["ok"] is False
    assert len(summary["results"]) == 4
    assert all(result["errorType"] == "connection" for result in summary["results"])
    provider.chat.assert_not_called()


def test_unlisted_model_is_retested_after_reprobe():
    """A model missing from a stale list gets a second look before being failed."""
    provider = MagicMock()
    provider.health.side_effect = [
        HealthResult(ok=True, url=DEFAULT_LMSTUDIO_URL, models=["other"], provider="openai_compat"),
        HealthResult(
            ok=True,
            url=DEFAULT_LMSTUDIO_URL,
            models=["other", "late-model"],
            provider="openai_compat",
        ),
    ]
    provider.chat.return_value = ChatResult(message=ProviderMessage(content="OK"))

    health = provider.health()
    result = probe_model(provider, health, "late-model")

    assert result["ok"] is True
    provider.chat.assert_called_once()


def test_model_test_provider_uses_dedicated_timeout():
    initialize()
    reset_workflow_settings()
    save_workflow_settings({"modelTestTimeoutSec": 900})

    provider = build_test_provider(DEFAULT_LMSTUDIO_URL)

    assert provider.timeout_sec == 900
    # Health probes stay bounded so an unreachable server still fails quickly.
    assert provider.health_timeout_sec == MAX_HEALTH_TIMEOUT_SEC
    reset_workflow_settings()


def test_health_probe_honours_raised_timeout():
    provider = OpenAICompatProvider(DEFAULT_LMSTUDIO_URL)
    provider.health_timeout_sec = 25.0
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {"data": [{"id": "loaded-model"}]}

    with patch("backend.services.llm_provider.requests.get", return_value=response) as mock_get:
        provider.health()

    assert mock_get.call_args.kwargs["timeout"] == 25.0


def test_lmstudio_unload_skips_the_model_under_test():
    provider = OpenAICompatProvider(DEFAULT_LMSTUDIO_URL)
    listed = MagicMock()
    listed.status_code = 200
    listed.json.return_value = {
        "models": [
            {
                "key": "google/gemma-3-12b",
                "loaded_instances": [{"id": "google/gemma-3-12b"}],
            },
            {
                "key": "qwen/qwen2.5-coder-14b",
                "loaded_instances": [{"id": "qwen/qwen2.5-coder-14b"}],
            },
        ]
    }
    unloaded = MagicMock()
    unloaded.status_code = 200
    unloaded.json.return_value = {"instance_id": "qwen/qwen2.5-coder-14b"}

    with (
        patch("backend.services.llm_provider.requests.get", return_value=listed) as mock_get,
        patch("backend.services.llm_provider.requests.post", return_value=unloaded) as mock_post,
    ):
        provider.unload_loaded_except("google/gemma-3-12b")

    assert mock_get.call_args.args[0] == "http://localhost:1234/api/v1/models"
    mock_post.assert_called_once()
    assert mock_post.call_args.args[0] == "http://localhost:1234/api/v1/models/unload"
    assert mock_post.call_args.kwargs["json"] == {"instance_id": "qwen/qwen2.5-coder-14b"}


def test_lmstudio_missing_native_unload_api_does_not_fail_probe():
    provider = MagicMock()
    provider.health.return_value = HealthResult(
        ok=True,
        url=DEFAULT_LMSTUDIO_URL,
        models=["keep-model"],
        provider="openai_compat",
    )
    provider.unload_loaded_except.side_effect = RuntimeError("404")
    provider.chat.return_value = ChatResult(message=ProviderMessage(content="OK"))

    result = probe_model(provider, provider.health.return_value, "keep-model")

    assert result["ok"] is True
    provider.chat.assert_called_once()


def test_lmstudio_unload_reports_unavailable_on_older_server():
    """A 404 means no native /api/v1; that must be visible, not silently skipped."""
    provider = OpenAICompatProvider(DEFAULT_LMSTUDIO_URL)
    missing = MagicMock()
    missing.status_code = 404

    with patch("backend.services.llm_provider.requests.get", return_value=missing):
        info = provider.unload_loaded_except("any-model")

    assert info["status"] == "unavailable"
    assert "0.4.0" in info["detail"]


def test_lmstudio_load_requests_small_context():
    provider = OpenAICompatProvider(DEFAULT_LMSTUDIO_URL)
    loaded = MagicMock()
    loaded.status_code = 200
    loaded.json.return_value = {
        "instance_id": "qwen/qwen3-27b",
        "load_time_seconds": 9.1,
        "load_config": {"context_length": 4096},
    }

    with patch("backend.services.llm_provider.requests.post", return_value=loaded) as mock_post:
        info = provider.load_model_for_test("qwen/qwen3-27b", context_length=4096)

    assert mock_post.call_args.args[0] == "http://localhost:1234/api/v1/models/load"
    assert mock_post.call_args.kwargs["json"]["context_length"] == 4096
    assert mock_post.call_args.kwargs["json"]["model"] == "qwen/qwen3-27b"
    assert info["status"] == "loaded"
    assert info["config"]["context_length"] == 4096


def test_probe_reports_guardrail_refusal_without_generating():
    """A refused load is its own failure, not a confusing generation error."""
    provider = MagicMock()
    health = HealthResult(
        ok=True,
        url=DEFAULT_LMSTUDIO_URL,
        models=["qwen/qwen3-27b"],
        provider="openai_compat",
    )
    provider.health.return_value = health
    provider.unload_loaded_except.return_value = {"status": "none", "unloaded": []}
    provider.load_model_for_test.return_value = {
        "status": "error",
        "error": "HTTP 400: insufficient system resources, requires approximately 19.54GB",
    }

    result = probe_model(provider, health, "qwen/qwen3-27b")

    assert result["ok"] is False
    assert result["errorType"] == "load"
    assert "19.54GB" in result["error"]
    provider.chat.assert_not_called()


def test_probe_records_context_and_unload_notes_on_success():
    provider = MagicMock()
    health = HealthResult(
        ok=True,
        url=DEFAULT_LMSTUDIO_URL,
        models=["keep-model"],
        provider="openai_compat",
    )
    provider.health.return_value = health
    provider.unload_loaded_except.return_value = {
        "status": "unloaded",
        "unloaded": ["other-model"],
    }
    provider.load_model_for_test.return_value = {
        "status": "loaded",
        "config": {"context_length": 4096},
    }
    provider.chat.return_value = ChatResult(message=ProviderMessage(content="OK"))

    result = probe_model(provider, health, "keep-model")

    assert result["ok"] is True
    assert result["contextLength"] == 4096
    assert "other-model" in result["unloadStatus"]


def test_probe_continues_when_explicit_load_is_unavailable():
    provider = MagicMock()
    health = HealthResult(
        ok=True,
        url=DEFAULT_LMSTUDIO_URL,
        models=["keep-model"],
        provider="openai_compat",
    )
    provider.health.return_value = health
    provider.unload_loaded_except.return_value = {"status": "none", "unloaded": []}
    provider.load_model_for_test.return_value = {
        "status": "unavailable",
        "detail": "native /api/v1 model API not found (needs LM Studio 0.4.0+)",
    }
    provider.chat.return_value = ChatResult(message=ProviderMessage(content="OK"))

    result = probe_model(provider, health, "keep-model")

    assert result["ok"] is True
    assert "0.4.0" in result["loadStatus"]
    provider.chat.assert_called_once()


def test_ollama_unload_uses_ps_list_except_keep_model():
    provider = OllamaProvider("http://localhost:11434")
    listed = MagicMock()
    listed.status_code = 200
    listed.json.return_value = {
        "models": [{"name": "keep-model"}, {"name": "other-model"}],
    }
    provider.unload = MagicMock(return_value=True)

    with patch("backend.services.llm_provider.requests.get", return_value=listed) as mock_get:
        provider.unload_loaded_except("keep-model")

    assert "/api/ps" in mock_get.call_args.args[0]
    provider.unload.assert_called_once_with("other-model")


def _await_job_done(client, *, attempts: int = 100) -> dict:
    for _ in range(attempts):
        data = client.get("/api/llm/test-agent-models/status").json()
        if data["status"] == "done":
            return data
        time.sleep(0.05)
    raise AssertionError("model test job did not finish")


def test_agent_model_tests_start_returns_pending_rows_then_completes():
    from backend import state

    client = _test_client()
    reset_job_state()
    state.PRIMARY_MODELS = {key: f"{key}-model" for key in ("po", "dev", "cr", "qa")}
    state.BACKUP_MODELS = {key: "" for key in ("po", "dev", "cr", "qa")}
    provider = MagicMock()
    provider.health.return_value = HealthResult(
        ok=True,
        url=DEFAULT_LMSTUDIO_URL,
        models=[f"{key}-model" for key in ("po", "dev", "cr", "qa")],
        provider="openai_compat",
    )
    provider.chat.return_value = ChatResult(message=ProviderMessage(content="OK"))

    with patch("backend.services.model_test_runner.build_test_provider", return_value=provider):
        started = client.post(
            "/api/llm/test-agent-models",
            json={"url": DEFAULT_LMSTUDIO_URL},
        ).json()

        # Every slot is returned up front so the UI can render live rows.
        assert started["runId"]
        assert started["total"] == 4
        assert [row["status"] for row in started["results"]] == ["pending"] * 4

        done = _await_job_done(client)

    assert done["ok"] is True
    assert done["completed"] == 4
    assert all(row["status"] == "passed" for row in done["results"])
    assert done["currentModel"] is None
    reset_job_state()


def test_second_start_reuses_the_running_job():
    from backend import state

    client = _test_client()
    reset_job_state()
    state.PRIMARY_MODELS = {key: f"{key}-model" for key in ("po", "dev", "cr", "qa")}
    state.BACKUP_MODELS = {key: "" for key in ("po", "dev", "cr", "qa")}
    release = threading.Event()
    provider = MagicMock()
    provider.health.return_value = HealthResult(
        ok=True,
        url=DEFAULT_LMSTUDIO_URL,
        models=[f"{key}-model" for key in ("po", "dev", "cr", "qa")],
        provider="openai_compat",
    )

    def _blocking_chat(*_args, **_kwargs):
        release.wait(timeout=10)
        return ChatResult(message=ProviderMessage(content="OK"))

    provider.chat.side_effect = _blocking_chat

    with patch("backend.services.model_test_runner.build_test_provider", return_value=provider):
        first = client.post("/api/llm/test-agent-models", json={}).json()
        second = client.post("/api/llm/test-agent-models", json={}).json()

        assert second["runId"] == first["runId"]
        assert second["status"] == "running"

        release.set()
        done = _await_job_done(client)

    assert done["completed"] == 4
    reset_job_state()


def test_lmstudio_ui_has_model_tests_and_log_fallback():
    root = Path(__file__).resolve().parents[1]
    settings = (root / "frontend/src/components/SettingsSlideOver.tsx").read_text(
        encoding="utf-8"
    )
    models = (root / "frontend/src/components/InstalledModelsPanel.tsx").read_text(
        encoding="utf-8"
    )
    logs = (root / "frontend/src/components/OllamaServiceLogPanel.tsx").read_text(
        encoding="utf-8"
    )
    app = (root / "frontend/src/App.tsx").read_text(encoding="utf-8")

    workflow = (root / "frontend/src/components/WorkflowPanel.tsx").read_text(encoding="utf-8")

    assert "Test connection" in settings
    assert "handleTestSlot" in settings
    assert "Test all agent models" not in settings
    assert "Test {modelFocus} model" not in settings
    assert "unloads other loaded models first" in settings
    # Unload / load problems are visible on the row, not swallowed.
    assert "result.unloadStatus" in settings
    assert "result.loadStatus" in settings
    assert "modelTestTimeoutSec" in workflow
    assert "Loaded models" in models
    assert "No model IDs returned by /v1/models" in models
    assert "Native LM Studio server logs are unavailable" in logs
    assert "Open app LLM logs" in logs
    assert "label: 'LLM Server'" in app
