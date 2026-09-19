"""Adaptive Ollama num_ctx: low start and bump on overflow."""

from backend.services.prompt_budget import (
    bump_ollama_num_ctx,
    initial_ollama_num_ctx,
    packed_prompt_num_ctx,
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


def test_packed_prompt_num_ctx_rounds_and_clamps():
    messages = [{"role": "user", "content": "x" * 4000}]
    # 4000 chars / 4 = 1000 tokens + 1024 headroom → 2048, packed floor 4096
    assert packed_prompt_num_ctx(messages, 32768) == 4096
    big = [{"role": "user", "content": "y" * 40000}]
    # 10000 tokens + 1024 → 11024 rounded up to 11264
    assert packed_prompt_num_ctx(big, 32768) == 11264
    assert packed_prompt_num_ctx(big, 4096) == 4096


def test_packed_prompt_num_ctx_counts_tools():
    messages = [{"role": "user", "content": "hello"}]
    assert packed_prompt_num_ctx(messages, 32768) == 4096
    tools = [{"function": {"name": "x", "description": "y" * 20000, "parameters": {}}}]
    # ~5000 tool tokens + 1024 headroom → 6144 after rounding
    assert packed_prompt_num_ctx(messages, 32768, tools=tools) == 6144


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


def test_packed_num_ctx_from_messages_ignores_32k_ceiling_when_prompt_is_small(monkeypatch):
    from backend.agents import scrum_agent
    from backend.agents.scrum_agent import ScrumAgent
    from backend.services import workflow_settings as ws_mod

    agent = ScrumAgent("dev", "test-model", "sys")
    ws = {"ollamaNumCtx": 32768, "ollamaNumCtxAdaptive": False}
    monkeypatch.setattr(scrum_agent, "get_workflow_settings", lambda: ws)
    monkeypatch.setattr(ws_mod, "get_workflow_settings", lambda: ws)
    agent._ensure_packed_num_ctx([{"role": "user", "content": "hello"}])
    assert agent._effective_num_ctx() == 4096


def test_packed_num_ctx_from_messages_includes_tools(monkeypatch):
    from backend.agents import scrum_agent
    from backend.agents.scrum_agent import ScrumAgent
    from backend.services import workflow_settings as ws_mod

    agent = ScrumAgent("dev", "test-model", "sys")
    ws = {"ollamaNumCtx": 32768, "ollamaNumCtxAdaptive": False}
    monkeypatch.setattr(scrum_agent, "get_workflow_settings", lambda: ws)
    monkeypatch.setattr(ws_mod, "get_workflow_settings", lambda: ws)
    tools = [{"function": {"name": "x", "description": "y" * 20000, "parameters": {}}}]
    agent._ensure_packed_num_ctx([{"role": "user", "content": "hello"}], tools=tools)
    assert agent._effective_num_ctx() == 6144


def test_describe_num_ctx_clamp_at_vram_floor():
    from backend.services.prompt_budget import describe_num_ctx_clamp

    ws = {"ollamaNumCtx": 32768, "ollamaNumCtxAuto": True, "ollamaNumCtxByRole": {}}
    info = describe_num_ctx_clamp("dev", settings=ws, effective=4096)
    assert info["requested"] == 32768
    assert info["effective"] == 4096
    assert info["clamped"] is True
    assert info["atFloor"] is True
    assert "32768" in info["label"]
    assert "4096" in info["label"]
    assert "VRAM" in info["label"]


def test_prompt_fills_ctx_window_and_tight_brief():
    from backend.services.prompt_budget import prompt_fills_ctx_window, truncate_brief

    huge = [{"role": "user", "content": "x" * 20000}]
    assert prompt_fills_ctx_window(huge, 4096) is True
    assert prompt_fills_ctx_window([{"role": "user", "content": "hi"}], 4096) is False
    trimmed = truncate_brief("word " * 2000, 4096)
    assert len(trimmed) <= 1600


def test_po_preload_budget_tighter_at_4096():
    from backend.services.prompt_budget import PACKED_NUM_CTX_FLOOR, sprint_preload_budgets

    dev = sprint_preload_budgets(PACKED_NUM_CTX_FLOOR, local_slm=False, role="dev")
    po = sprint_preload_budgets(PACKED_NUM_CTX_FLOOR, local_slm=False, role="po")
    assert po["total"] < dev["total"]
    assert po["semantic"] <= 400


def test_should_force_patch_on_read_only_exit():
    from backend.services.sprint_speed_gates import should_force_patch_next_dev_step

    assert should_force_patch_next_dev_step(
        {"lastStepOutcome": {"exitReason": "read_only_no_edits"}}
    )


def test_length_truncation_detected_even_when_adaptive_off():
    from backend.agents.scrum_agent import ScrumAgent

    agent = ScrumAgent("dev", "test-model", "sys")
    agent._last_token_usage = {
        "doneReason": "length",
        "promptTokens": 4089,
        "evalTokens": 7,
        "numCtx": 4096,
    }
    assert agent._generation_was_length_truncated() is True
    agent._length_ctx_retried = False
    bumped = {"n": 0}

    def fake_bump():
        bumped["n"] += 1
        return True

    agent._bump_num_ctx_on_overflow = fake_bump  # type: ignore[method-assign]
    assert agent._retry_on_length_truncation([{"role": "user", "content": "hi"}]) is True
    assert bumped["n"] == 1
    assert agent._retry_on_length_truncation([{"role": "user", "content": "hi"}]) is False
