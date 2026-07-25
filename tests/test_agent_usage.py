"""Per-agent usage: Ollama token extract + task rollup."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from backend import state
from backend.agents.task_context import init_new_task
from backend.bootstrap import initialize
from backend.services.agent_usage import (
    extract_ollama_token_counts,
    record_agent_usage,
    record_step_usage_from_trace,
)


def _clear_board():
    state.SHARED_BOARD.clear()
    for lane in (
        "Features",
        "Backlog",
        "Pending Approval",
        "Refinement",
        "In Progress",
        "Needs User",
        "Needs PO",
        "Code Review",
        "QA",
        "Done",
    ):
        state.SHARED_BOARD[lane] = []


def test_extract_ollama_token_counts_from_attrs():
    resp = SimpleNamespace(prompt_eval_count=1200, eval_count=80)
    p, e, total, reported = extract_ollama_token_counts(resp)
    assert reported is True
    assert p == 1200
    assert e == 80
    assert total == 1280


def test_extract_ollama_token_counts_from_dict():
    p, e, total, reported = extract_ollama_token_counts(
        {"prompt_eval_count": 10, "eval_count": 5}
    )
    assert (p, e, total, reported) == (10, 5, 15, True)


def test_extract_ollama_token_counts_missing():
    p, e, total, reported = extract_ollama_token_counts(SimpleNamespace())
    assert reported is False
    assert (p, e, total) == (0, 0, 0)
    assert extract_ollama_token_counts(None)[3] is False


def test_record_agent_usage_accumulates_two_steps():
    initialize()
    _clear_board()
    task = init_new_task(
        {"id": "TASK-USAGE-1", "title": "Usage", "status": "In Progress"}
    )
    state.SHARED_BOARD["In Progress"] = [task]

    record_agent_usage(
        "TASK-USAGE-1",
        "Developer",
        duration_ms=1000,
        ollama_ms=800,
        tool_ms=100,
        prompt_tokens=100,
        eval_tokens=20,
        call_count=2,
        step_count=1,
        tokens_reported=True,
    )
    record_agent_usage(
        "TASK-USAGE-1",
        "Developer",
        duration_ms=2000,
        ollama_ms=1500,
        tool_ms=200,
        prompt_tokens=50,
        eval_tokens=10,
        call_count=1,
        step_count=1,
        tokens_reported=True,
    )

    entry = task["agentUsage"]["Developer"]
    assert entry["stepCount"] == 2
    assert entry["callCount"] == 3
    assert entry["durationMs"] == 3000
    assert entry["ollamaMs"] == 2300
    assert entry["toolMs"] == 300
    assert entry["promptTokens"] == 150
    assert entry["evalTokens"] == 30
    assert entry["totalTokens"] == 180
    assert entry["tokensReported"] is True


def test_record_step_usage_from_trace():
    initialize()
    _clear_board()
    task = init_new_task(
        {"id": "TASK-USAGE-2", "title": "Trace", "status": "In Progress"}
    )
    state.SHARED_BOARD["In Progress"] = [task]

    # Pretend live ollama bumps already happened
    record_agent_usage(
        "TASK-USAGE-2",
        "Developer",
        ollama_ms=500,
        prompt_tokens=40,
        eval_tokens=5,
        call_count=1,
        tokens_reported=True,
    )

    from datetime import datetime, timedelta

    trace = SimpleNamespace(
        task_id="TASK-USAGE-2",
        agent="Developer",
        started_monotonic=datetime.now() - timedelta(seconds=3),
        tools_log=[{"toolName": "run_command", "durationMs": 250, "success": True}],
    )
    record_step_usage_from_trace(trace)
    entry = task["agentUsage"]["Developer"]
    assert entry["stepCount"] == 1
    assert entry["toolMs"] == 250
    assert entry["durationMs"] >= 2500
    assert entry["callCount"] == 1
    assert entry["promptTokens"] == 40


def test_ui_markers_for_agent_usage():
    root = Path(__file__).resolve().parents[1]
    modal = (root / "frontend" / "src" / "components" / "TaskDetailModal.tsx").read_text(
        encoding="utf-8"
    )
    assert "Agent usage" in modal
    card = (root / "frontend" / "src" / "components" / "TaskCard.tsx").read_text(
        encoding="utf-8"
    )
    assert "formatAgentUsageBrief" in card
    bar = (root / "frontend" / "src" / "components" / "AgentRunBar.tsx").read_text(
        encoding="utf-8"
    )
    assert "promptTokensTotal" in bar or "Tokens" in bar
    types = (root / "frontend" / "src" / "types" / "index.ts").read_text(encoding="utf-8")
    assert "AgentUsageEntry" in types
    assert "agentUsage?" in types
