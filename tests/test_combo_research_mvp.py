"""Tests for Dev core memory block + offline prompt optimize metric."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from backend.agents.scrum_agent import ScrumAgent
from backend.services.prompt_optimize import (
    heuristic_improve_prompt,
    mean_trace_score,
    score_step_trace,
)
from backend.services.workflow_settings import DEFAULT_WORKFLOW_SETTINGS
from backend.storage.memory_engine import (
    CORE_BLOCK_CATEGORY,
    CORE_BLOCK_MAX_CHARS,
    SemanticMemoryEngine,
)


@pytest.fixture
def mem_engine(tmp_path: Path) -> SemanticMemoryEngine:
    eng = SemanticMemoryEngine(db_path=str(tmp_path / "mem.db"), ollama_url="http://127.0.0.1:9")
    with patch.object(eng, "_embed_ollama", return_value=None):
        yield eng


def test_defaults_enable_dev_core_memory_block():
    assert DEFAULT_WORKFLOW_SETTINGS.get("enableDevCoreMemoryBlock") is True


def test_core_block_merge_respects_cap(mem_engine: SemanticMemoryEngine):
    with patch.object(mem_engine, "_embed_ollama", return_value=None):
        for i in range(40):
            mem_engine.merge_lesson_into_core_block(
                "Developer",
                f"lesson number {i} " + ("x" * 40),
                project_id="proj-core",
                max_chars=CORE_BLOCK_MAX_CHARS,
            )
        block = mem_engine.get_core_block("Developer", project_id="proj-core")
    assert block is not None
    assert block["category"] == CORE_BLOCK_CATEGORY
    assert len(block["content"]) <= CORE_BLOCK_MAX_CHARS
    assert "lesson number 39" in block["content"]
    assert "lesson number 0" not in block["content"]


def test_core_block_excluded_from_search(mem_engine: SemanticMemoryEngine):
    with patch.object(mem_engine, "_embed_ollama", return_value=None):
        mem_engine.upsert_core_block(
            "- always use apply_patch for edits",
            "Developer",
            project_id="proj-s",
        )
        mem_engine.save(
            "Developer",
            "other fix pattern about widgets",
            "fix_pattern",
            project_id="proj-s",
        )
        hits = mem_engine.search("Developer", "apply_patch widgets", limit=5, project_id="proj-s")
    assert all(str(h.get("category")) != CORE_BLOCK_CATEGORY for h in hits)


def test_dev_user_content_injects_core_block_without_search_hits(mem_engine: SemanticMemoryEngine):
    with patch.object(mem_engine, "_embed_ollama", return_value=None):
        mem_engine.upsert_core_block("- sticky convention", "Developer", project_id="proj-inj")
    agent = ScrumAgent(role="Developer", model="test", system_prompt="sys")
    agent.memory = mem_engine
    with patch(
        "backend.agents.scrum_agent.get_workflow_settings",
        return_value={
            "enableDevCoreMemoryBlock": True,
            "promptProfile": "local_slm",
            "localSlmSprintPreload": False,
            "agentEfficiencyMode": "standard",
        },
    ):
        with patch("backend.state.CURRENT_PROJECT_ID", "proj-inj"):
            with patch("backend.state.ACTIVE_SPRINT_TASK_ID", None):
                with patch(
                    "backend.services.prompt_profile.is_local_slm_profile",
                    return_value=True,
                ):
                    with patch(
                        "backend.services.prompt_profile.local_slm_sprint_preload_enabled",
                        return_value=False,
                    ):
                        content = agent._build_user_content("Implement feature X")
    assert "=== DEV CORE MEMORY ===" in content
    assert "sticky convention" in content
    assert "Task Detail:" in content
    assert "=== RELEVANT HISTORICAL MEMORIES ===" not in content


def test_score_step_trace_rewards_writes_penalizes_thrash():
    good = score_step_trace(
        {
            "ok": True,
            "exitReason": "completed_with_writes",
            "toolsUsed": ["read_file", "apply_patch"],
            "llmCalls": 4,
            "toolCalls": 3,
            "failedTools": 0,
        }
    )
    bad = score_step_trace(
        {
            "ok": False,
            "exitReason": "max_iterations",
            "toolsUsed": ["read_file"],
            "llmCalls": 40,
            "toolCalls": 2,
            "failedTools": 5,
        }
    )
    assert good > bad
    assert good >= 0.7
    assert bad <= 0.45
    assert score_step_trace({}) == 0.5


def test_heuristic_improve_prompt_appends_lessons():
    seed = "You are the Developer."
    traces = [
        {"score": 0.2, "stopReason": "max_iterations", "tools": []},
        {"score": 0.3, "stopReason": "duplicate_tool", "tools": []},
    ]
    out, method = heuristic_improve_prompt(seed, traces)
    assert method == "heuristic"
    assert "Efficiency lessons" in out
    assert "apply_patch" in out.lower() or "fingerprint" in out.lower()
    assert mean_trace_score(traces) < 0.5
