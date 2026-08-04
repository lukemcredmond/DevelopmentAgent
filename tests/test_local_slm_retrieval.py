"""Bounded Qdrant/file/packer preload for local_slm profile."""

from unittest.mock import MagicMock, patch

from backend import state
from backend.bootstrap import initialize
from backend.services.prompt_budget import (
    LOCAL_SLM_SEMANTIC_CAP,
    LOCAL_SLM_TOTAL_PRELOAD_CAP,
    sprint_preload_budgets,
)
from backend.services.prompt_profile import local_slm_sprint_preload_enabled
from backend.services.workflow_settings import save_workflow_settings


def test_sprint_preload_budgets_local_smaller_than_full():
    full = sprint_preload_budgets(32768, local_slm=False)
    local = sprint_preload_budgets(32768, local_slm=True)
    assert local["total"] <= LOCAL_SLM_TOTAL_PRELOAD_CAP
    assert local["semantic"] <= LOCAL_SLM_SEMANTIC_CAP
    assert local["total"] < full["total"]
    assert local["semantic"] < full["semantic"]


def test_local_slm_preload_toggle():
    save_workflow_settings({"promptProfile": "local_slm", "localSlmSprintPreload": False})
    assert local_slm_sprint_preload_enabled() is False
    save_workflow_settings({"promptProfile": "local_slm", "localSlmSprintPreload": True})
    assert local_slm_sprint_preload_enabled() is True
    save_workflow_settings({"promptProfile": "full"})
    assert local_slm_sprint_preload_enabled() is True


def test_inject_sprint_context_local_slm_calls_semantic_with_small_budget(tmp_path, monkeypatch):
    monkeypatch.setenv("ALLHANDS_HOME", str(tmp_path))
    initialize()
    save_workflow_settings(
        {
            "promptProfile": "local_slm",
            "localSlmSprintPreload": True,
            "contextPacker": "off",
        }
    )
    state.PROJECT_BRIEF = "Brief"
    task = {
        "id": "T-LSLM",
        "title": "Feature",
        "description": "Do thing",
        "acceptanceCriteria": ["AC1"],
        "status": "In Progress",
    }

    from backend.services.sprint_service import _inject_sprint_context

    seen: dict = {}

    def fake_semantic(task, max_chars, top_k_override=None):
        seen["max_chars"] = max_chars
        seen["top_k_override"] = top_k_override
        return "\n=== SEMANTIC CODE CONTEXT (from index) ===\nchunk\n", ["a.py"]

    with patch(
        "backend.storage.code_index.build_semantic_sprint_context",
        side_effect=fake_semantic,
    ):
        with patch(
            "backend.services.sprint_service.build_sprint_file_context",
            return_value=("", []),
        ):
            prompt = _inject_sprint_context(task, state.PROJECT_BRIEF, "Developer", "Step.")

    assert "SEMANTIC CODE CONTEXT" in prompt
    assert seen["max_chars"] <= LOCAL_SLM_SEMANTIC_CAP
    assert seen["top_k_override"] == 2


def test_inject_sprint_context_local_slm_skips_when_preload_off(tmp_path, monkeypatch):
    monkeypatch.setenv("ALLHANDS_HOME", str(tmp_path))
    initialize()
    save_workflow_settings(
        {"promptProfile": "local_slm", "localSlmSprintPreload": False, "contextPacker": "off"}
    )
    state.PROJECT_BRIEF = "Brief"
    task = {
        "id": "T-OFF",
        "title": "Feature",
        "description": "Do thing",
        "acceptanceCriteria": [],
        "status": "In Progress",
    }

    from backend.services.sprint_service import _inject_sprint_context

    with patch(
        "backend.storage.code_index.build_semantic_sprint_context",
        side_effect=AssertionError("should not preload"),
    ):
        prompt = _inject_sprint_context(task, state.PROJECT_BRIEF, "Developer", "Step.")

    assert "SEMANTIC CODE CONTEXT" not in prompt


def test_scrum_agent_local_slm_memory_truncated():
    from backend.agents.scrum_agent import ScrumAgent

    save_workflow_settings({"promptProfile": "local_slm", "localSlmSprintPreload": True})
    agent = ScrumAgent(role="Developer", model="m", system_prompt="sys")
    long_content = "x" * 800
    agent.memory = MagicMock()
    agent.memory.search.return_value = [{"category": "fix_pattern", "content": long_content}]

    with patch("backend.agents.scrum_agent.state") as mock_state:
        mock_state.CURRENT_PROJECT_ID = "p1"
        mock_state.ACTIVE_SPRINT_TASK_ID = None
        body = agent._build_user_content("implement")

    assert "RELEVANT HISTORICAL MEMORIES" in body
    assert long_content not in body
    assert "..." in body
    agent.memory.search.assert_called_once()
    assert agent.memory.search.call_args.kwargs.get("limit") == 2
