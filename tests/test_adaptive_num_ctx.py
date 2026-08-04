"""Adaptive Ollama num_ctx: low start and bump on overflow."""

from backend.services.prompt_budget import (
    bump_ollama_num_ctx,
    initial_ollama_num_ctx,
)


def test_initial_ollama_num_ctx_off_uses_ceiling():
    ws = {"ollamaNumCtx": 16384, "ollamaNumCtxAdaptive": False}
    assert initial_ollama_num_ctx("dev", settings=ws) == 16384


def test_initial_ollama_num_ctx_on_uses_start_capped_by_ceiling():
    ws = {
        "ollamaNumCtx": 32768,
        "ollamaNumCtxAdaptive": True,
        "ollamaNumCtxAdaptiveStart": 4096,
    }
    assert initial_ollama_num_ctx("dev", settings=ws) == 4096
    ws["ollamaNumCtxByRole"] = {"po": 8192}
    assert initial_ollama_num_ctx("po", settings=ws) == 4096
    ws["ollamaNumCtxAdaptiveStart"] = 12000
    assert initial_ollama_num_ctx("po", settings=ws) == 8192


def test_bump_ollama_num_ctx_doubles_or_steps():
    assert bump_ollama_num_ctx(4096, 32768, step=8192) == 12288
    assert bump_ollama_num_ctx(8192, 32768, step=8192) == 16384
    assert bump_ollama_num_ctx(20000, 32768, step=8192) == 32768
    assert bump_ollama_num_ctx(32768, 32768, step=8192) is None


def test_scrum_agent_effective_and_bump(monkeypatch):
    from backend.agents import scrum_agent
    from backend.agents.scrum_agent import ScrumAgent
    from backend.services import workflow_settings as ws_mod

    agent = ScrumAgent("dev", "test-model", "sys")
    ws = {
        "ollamaNumCtx": 16384,
        "ollamaNumCtxAdaptive": True,
        "ollamaNumCtxAdaptiveStart": 4096,
        "ollamaNumCtxAdaptiveStep": 4096,
    }
    monkeypatch.setattr(scrum_agent, "get_workflow_settings", lambda: ws)
    monkeypatch.setattr(ws_mod, "get_workflow_settings", lambda: ws)
    assert agent._effective_num_ctx() == 4096
    assert agent._bump_num_ctx_on_overflow() is True
    assert agent._effective_num_ctx() == 8192
    assert agent._bump_num_ctx_on_overflow() is True
    assert agent._effective_num_ctx() == 16384
    assert agent._bump_num_ctx_on_overflow() is False
