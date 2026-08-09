"""Cards without a description key must not crash sprint prompt build."""

from backend.agents.task_context import build_task_prompt, normalize_task


def test_normalize_task_defaults_missing_description_and_title():
    task = {"id": "T-missing-desc"}
    normalize_task(task)
    assert task["description"] == ""
    assert task["title"] == ""


def test_build_task_prompt_without_description_key():
    task = {
        "id": "T-title-only",
        "title": "Title only card",
        "acceptanceCriteria": ["Does something"],
    }
    # Deliberately omit description — legacy/imported cards can do this.
    assert "description" not in task
    prompt = build_task_prompt(task, "Project brief")
    assert "Description:" in prompt
    assert "Title: Title only card" in prompt
    assert task["description"] == ""
