"""Step recap blocks for weaker local models."""

from types import SimpleNamespace

from backend.agents.task_context import init_new_task
from backend.services.step_recap import (
    STEP_GOAL_MARKER,
    STEP_RECAP_MARKER,
    build_step_goal_anchor,
    build_step_recap_after_tools,
    step_recap_enabled,
)


def test_step_recap_enabled_by_default():
    assert step_recap_enabled({}) is True
    assert step_recap_enabled({"enableAgentStepRecap": False}) is False


def test_step_goal_anchor_includes_ac():
    task = init_new_task(
        {
            "id": "T-1",
            "title": "Verify firebase_auth",
            "acceptanceCriteria": ["firebase_auth:^4.16.0 in pubspec.yaml"],
        }
    )
    text = build_step_goal_anchor("Developer", task)
    assert STEP_GOAL_MARKER in text
    assert "firebase_auth" in text
    assert "Do not ask for the brief" in text


def test_step_recap_includes_intent_and_dedupe():
    task = init_new_task({"id": "T-2", "title": "Explore", "acceptanceCriteria": ["x"]})
    result = SimpleNamespace(success=True, duplicate_skip=False)
    batch = [("list_dir", {"path": "."}, result)]
    text = build_step_recap_after_tools(
        agent_role="Product Owner",
        task=task,
        batch=batch,
        tools_used_step={"list_dir"},
        successful_tool_keys=[("list_dir", '{"path": "."}')],
        iteration=1,
        max_iterations=8,
    )
    assert STEP_RECAP_MARKER in text
    assert "Intent:" in text
    assert "Do NOT repeat" in text
    assert "list_dir" in text
    assert "update_board" in text or "JSON" in text
