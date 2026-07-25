"""LLM-driven Tool Health probes (mocked Ollama chat)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from backend.bootstrap import initialize
from backend.services.tool_llm_probe import run_llm_tool_probe


def _chat_with_tool_call(tool_name: str, arguments: dict):
    def chat_fn(model, messages, tools, options):
        return SimpleNamespace(
            message=SimpleNamespace(
                content="",
                tool_calls=[
                    SimpleNamespace(
                        function=SimpleNamespace(name=tool_name, arguments=arguments),
                    )
                ],
            )
        )

    return chat_fn


def _chat_text_only(model, messages, tools, options):
    return SimpleNamespace(message=SimpleNamespace(content="I cannot call tools.", tool_calls=None))


def test_llm_probe_write_file_skips():
    initialize()
    result = run_llm_tool_probe("dev", "write_file", model="test-model", chat_fn=_chat_text_only)
    assert result["status"] == "skip"
    assert result["mode"] == "llm"
    assert result["skipReason"] == "destructive"
    assert result["model"] == "test-model"


def test_llm_probe_list_dir_pass_with_tool_call():
    initialize()
    result = run_llm_tool_probe(
        "dev",
        "list_dir",
        model="test-model",
        chat_fn=_chat_with_tool_call("list_dir", {"path": "."}),
    )
    assert result["mode"] == "llm"
    assert result["modelCalledTool"] is True
    assert result["status"] == "pass"
    assert result["success"] is True
    assert result["probeArgs"].get("path") == "."


def test_llm_probe_fails_when_model_returns_text_only():
    initialize()
    result = run_llm_tool_probe("dev", "list_dir", model="test-model", chat_fn=_chat_text_only)
    assert result["status"] == "fail"
    assert result["modelCalledTool"] is False
    assert "did not call" in result["output"].lower()


def test_llm_probe_api_skip_write_file():
    from fastapi.testclient import TestClient

    from backend.main import app

    initialize()
    client = TestClient(app)
    skip_res = client.post(
        "/api/tools/probe-llm",
        json={"agent": "dev", "toolName": "write_file", "model": "m1"},
    )
    assert skip_res.status_code == 200
    body = skip_res.json()["result"]
    assert body["status"] == "skip"
    assert body["mode"] == "llm"
    assert body["skipReason"] == "destructive"


def test_ui_llm_health_markers():
    root = Path(__file__).resolve().parents[1]
    health = (root / "frontend" / "src" / "components" / "ToolHealthPanel.tsx").read_text(
        encoding="utf-8"
    )
    assert "Ask model" in health
    assert "Ask model (all safe)" in health
    assert "Auto LLM-test on model pick" in health
    settings = (root / "frontend" / "src" / "components" / "SettingsSlideOver.tsx").read_text(
        encoding="utf-8"
    )
    assert "runAndPersistLlmProbeAll" in settings
    assert "assignModel" in settings
    readme = (root / "README.md").read_text(encoding="utf-8")
    assert "/api/tools/probe-llm" in readme
    assert "Ask model" in readme
