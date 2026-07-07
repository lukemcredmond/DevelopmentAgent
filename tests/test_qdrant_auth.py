"""Qdrant API key wiring tests."""

from unittest.mock import MagicMock, patch

from backend.services.qdrant_auth import qdrant_connection_settings, qdrant_request_headers


def test_qdrant_request_headers_with_key():
    headers = qdrant_request_headers("secret-key")
    assert headers == {"api-key": "secret-key"}


def test_qdrant_request_headers_without_key():
    assert qdrant_request_headers(None) == {}
    assert qdrant_request_headers("") == {}


@patch("backend.services.qdrant_auth.get_workflow_settings")
def test_code_index_engine_passes_api_key(mock_ws):
    mock_ws.return_value = {
        "qdrantUrl": "http://localhost:6333",
        "qdrantApiKey": "test-key",
        "embedModel": "nomic-embed-text",
    }
    mock_client_cls = MagicMock()
    mock_client = MagicMock()
    mock_client.get_collections.return_value.collections = []
    mock_client_cls.return_value = mock_client

    with patch("qdrant_client.QdrantClient", mock_client_cls):
        from backend.storage.code_index import CodeIndexEngine

        engine = CodeIndexEngine(project_id="test-proj")
        engine._get_client()

    mock_client_cls.assert_called_once()
    kwargs = mock_client_cls.call_args.kwargs
    assert kwargs.get("url") == "http://localhost:6333"
    assert kwargs.get("api_key") == "test-key"


@patch("backend.services.qdrant_auth.get_workflow_settings")
def test_qdrant_connection_settings(mock_ws):
    mock_ws.return_value = {"qdrantUrl": "http://qdrant:6333/", "qdrantApiKey": " abc "}
    url, key = qdrant_connection_settings("proj-1")
    assert url == "http://qdrant:6333"
    assert key == "abc"
