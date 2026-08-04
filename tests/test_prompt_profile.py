"""Tests for local_slm vs full prompt profiles."""

from unittest.mock import patch

import pytest

from backend.agents.registry import agent_po
from backend.services.prompt_defaults import get_effective_system_prompt
from backend.services.prompt_profile import (
    LOCAL_SLM_SYSTEM,
    is_local_slm_profile,
)
from backend.services.prompt_sections import FocusContext, compose_prompt
from backend.services.workflow_settings import reset_workflow_settings, save_workflow_settings


@pytest.fixture(autouse=True)
def _reset_workflow():
    reset_workflow_settings()
    yield
    reset_workflow_settings()


def _sample_task(**extra):
    base = {
        "id": "T-LOCAL",
        "title": "Feature X",
        "description": "Do the thing",
        "acceptanceCriteria": ["AC1", "AC2", "AC3", "AC4"],
        "files": ["src/a.py", "src/b.py"],
        "decisions": [{"agent": "Developer", "summary": "read_file src/a.py", "detail": "ok"}],
        "transcript": [{"role": "assistant", "content": "I'll read the file"}],
        "blockedBy": [],
        "status": "Needs PO",
    }
    base.update(extra)
    return base


def test_prompt_profile_local_slm_po_system_under_n_chars():
    save_workflow_settings({"promptProfile": "local_slm"})
    from backend.services.workflow_settings import get_workflow_settings

    ws = get_workflow_settings()
    po = get_effective_system_prompt("Product Owner", ws)
    full = get_effective_system_prompt("Product Owner", {"promptProfile": "full"})
    assert len(po) < len(full)
    assert len(po) < 2000


def test_local_slm_skips_skill_injection():
    save_workflow_settings({"promptProfile": "local_slm"})
    agent_po.assigned_skills = ["po-combined.md", "extra-skill.md"]
    with patch("builtins.open", side_effect=AssertionError("should not read skill files in local_slm")):
        ctx = agent_po._get_skills_context()
    assert "Huge skill" not in ctx
    assert "names only" in ctx.lower()
    assert "po-combined.md" in ctx
    agent_po.assigned_skills = []


def test_local_slm_compose_excludes_transcript_and_legacy_sections():
    save_workflow_settings({"promptProfile": "local_slm"})
    task = _sample_task()
    focus = FocusContext(
        agent_role="Product Owner",
        focus_mode="whole",
        include_full_spec=True,
    )
    out = compose_prompt(task, "Brief " * 200, [], focus, agent_role="Product Owner")
    assert "TASK TRANSCRIPT" not in out
    assert "PRIOR AGENT DECISIONS" not in out
    assert "Workspace files:" not in out


def test_full_profile_po_still_uses_legacy_monolith():
    save_workflow_settings({"promptProfile": "full"})
    assert not is_local_slm_profile()
    task = _sample_task()
    focus = FocusContext(
        agent_role="Product Owner",
        focus_mode="whole",
        include_full_spec=True,
    )
    out = compose_prompt(task, "Brief text", [], focus, agent_role="Product Owner")
    assert "PRIOR AGENT DECISIONS" in out or "TASK TRANSCRIPT" in out


def test_save_workflow_normalizes_prompt_profile():
    save_workflow_settings({"promptProfile": "lean"})
    from backend.services.workflow_settings import get_workflow_settings

    assert get_workflow_settings()["promptProfile"] == "local_slm"
    save_workflow_settings({"promptProfile": "full"})
    assert get_workflow_settings()["promptProfile"] == "full"


def test_local_slm_developer_system_is_lean():
    save_workflow_settings({"promptProfile": "local_slm"})
    from backend.services.workflow_settings import get_workflow_settings

    ws = get_workflow_settings()
    dev = get_effective_system_prompt("Developer", ws)
    assert dev == LOCAL_SLM_SYSTEM["Developer"]
    assert len(dev) < 500
