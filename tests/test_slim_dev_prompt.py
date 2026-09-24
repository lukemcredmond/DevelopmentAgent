"""Slim dev prompt for stuck / recovery cards."""

from backend.agents.task_context import init_new_task
from backend.services.sprint_service import _inject_sprint_context, _should_slim_dev_prompt


def test_should_slim_for_consecutive_bad_exits():
    task = {
        "title": "Build main app UI with tabs",
        "consecutiveBadExits": 2,
        "lastStepOutcome": {"exitReason": "text_rejection_loop"},
    }
    assert _should_slim_dev_prompt(task) is True


def test_slim_prompt_omits_heavy_sections():
    from unittest.mock import patch

    task = init_new_task(
        {
            "id": "T-SLIM",
            "title": "Build main app UI with tabs",
            "description": "Implement tabbed navigation for the meal planner app.",
            "status": "In Progress",
            "acceptanceCriteria": ["Tabs render", "Navigation works", "State persists"],
            "consecutiveBadExits": 3,
            "lastStepOutcome": {"exitReason": "text_rejection_loop"},
            "transcript": [{"role": "agent", "content": "x" * 5000} for _ in range(20)],
        }
    )
    brief = "Sprint brief for meal planner."
    instructions = "Call apply_patch or write_file."

    with patch(
        "backend.services.sprint_service.build_sprint_file_context",
        return_value=("", []),
    ), patch(
        "backend.storage.code_index.build_semantic_sprint_context",
        return_value=("SEMANTIC_BLOCK" * 50, ["lib/a.dart"]),
    ):
        slim = _inject_sprint_context(task, brief, "Developer", instructions)
        task_full = dict(task)
        task_full["consecutiveBadExits"] = 0
        task_full.pop("lastStepOutcome", None)
        full = _inject_sprint_context(task_full, brief, "Developer", instructions)

    assert "SEMANTIC_BLOCK" not in slim
    assert "SEMANTIC_BLOCK" in full
    assert "LAST STEP OUTCOME" in slim
    assert len(slim) < len(full)
