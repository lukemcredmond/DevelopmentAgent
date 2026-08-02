"""Tests for composable sprint prompt sections."""

from backend.services.prompt_sections import FocusContext, compose_prompt, build_section


def test_ac_focus_includes_single_criterion():
    task = {
        "id": "T1",
        "title": "Login",
        "description": "Add login",
        "acceptanceCriteria": ["User can log in", "Invalid password shows error"],
        "files": [],
        "decisions": [],
        "transcript": [],
        "blockedBy": [],
        "status": "In Progress",
    }
    focus = FocusContext(
        agent_role="Developer",
        focus_mode="ac",
        ac_index=1,
        include_full_spec=False,
    )
    block = build_section("ac_focus", task, "brief", focus=focus, limits=_limits())
    assert "this step only" in block.lower()
    assert "Invalid password" in block
    assert "User can log in" not in block


def test_compose_focus_excludes_full_ac_list():
    task = {
        "id": "T2",
        "title": "API",
        "description": "Build API",
        "acceptanceCriteria": ["A", "B", "C"],
        "files": [],
        "decisions": [],
        "transcript": [],
        "blockedBy": [],
        "status": "In Progress",
        "focusMode": "ac",
        "focusAcIndex": 0,
    }
    focus = FocusContext(
        agent_role="Developer",
        focus_mode="ac",
        ac_index=0,
        include_full_spec=False,
    )
    out = compose_prompt(
        task,
        "project brief",
        ["card_core", "ac_focus"],
        focus,
        agent_role="Developer",
    )
    assert "A" in out
    assert "- B" not in out and "\n- C" not in out


def _limits():
    from backend.services.prompt_sections import _PromptLimits

    return _PromptLimits(
        slim=False,
        decision_limit=8,
        transcript_limit=6,
        related_limit=5,
        dependency_limit=10,
        num_ctx=8192,
    )
