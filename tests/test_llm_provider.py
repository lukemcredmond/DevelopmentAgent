"""LLM provider mapping, health, embed split, and Ollama option regression."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from backend.bootstrap import initialize
from backend.services.llm_provider import (
    DEFAULT_LMSTUDIO_URL,
    DEFAULT_OLLAMA_URL,
    OpenAICompatProvider,
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


def test_openai_compat_chat_omits_num_ctx():
    provider = OpenAICompatProvider("http://localhost:1234/v1")
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {
        "choices": [{"message": {"role": "assistant", "content": "hi", "tool_calls": []}}],
        "usage": {"prompt_tokens": 2, "completion_tokens": 1},
    }
    with patch("backend.services.llm_provider.requests.post", return_value=response) as mock_post:
        provider.chat(
            "local",
            [{"role": "user", "content": "hi"}],
            options={"temperature": 0.1, "num_ctx": 32768, "keep_alive": "30m"},
        )
    payload = mock_post.call_args.kwargs["json"]
    assert "num_ctx" not in payload
    assert payload["messages"][0]["content"] == "hi"


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
