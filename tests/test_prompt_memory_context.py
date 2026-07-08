"""Prompt context: decisions, refinement notes, and semantic memory injection."""

from unittest.mock import patch

from backend.bootstrap import initialize


def test_build_task_prompt_includes_decision_and_refinement_notes():
    initialize()
    from backend import state
    from backend.agents.task_context import build_task_prompt, init_new_task, record_task_decision

    task = init_new_task(
        {
            "id": "T-CTX",
            "title": "Auth middleware",
            "description": "Add JWT",
            "acceptanceCriteria": ["Tokens validated"],
            "status": "Backlog",
            "refinementNotes": "Use existing bearer middleware pattern.",
            "spikeReport": '{"findings": "Found JwtBearer in Program.cs"}',
        }
    )
    state.SHARED_BOARD = {"Backlog": [task]}
    record_task_decision("T-CTX", "Developer", "refinement_dev", "Needs spike", "detail")

    prompt = build_task_prompt(task, "Build secure API")
    assert "PRIOR AGENT DECISIONS" in prompt
    assert "Needs spike" in prompt
    assert "REFINEMENT NOTES" in prompt
    assert "bearer middleware" in prompt
    assert "SPIKE REPORT" in prompt
    assert "JwtBearer" in prompt


def test_decisions_persist_in_board_save():
    initialize()
    from backend import state
    from backend.agents.task_context import init_new_task, record_task_decision
    from backend.services.project_service import save_current_project_state

    task = init_new_task(
        {
            "id": "T-SAVE",
            "title": "Persist test",
            "description": "d",
            "status": "Backlog",
        }
    )
    state.SHARED_BOARD = {"Backlog": [task]}
    record_task_decision("T-SAVE", "Developer", "claim", "Saved decision")
    save_current_project_state()

    raw = state.storage.load_project(state.CURRENT_PROJECT_ID)
    assert raw is not None
    board = raw["board_state"]
    saved = next(t for lane in board.values() for t in lane if t.get("id") == "T-SAVE")
    assert any(d.get("summary") == "Saved decision" for d in saved.get("decisions") or [])


@patch("backend.agents.scrum_agent.ScrumAgent.memory", create=True)
def test_build_user_content_includes_memory_block(mock_memory_prop):
    initialize()
    from backend.agents.registry import agent_dev

    agent_dev.memory.search = lambda role, query, limit=3, project_id=None, **kwargs: [
        {"category": "fix_pattern", "content": "write_file auth.js succeeded"}
    ]
    content = agent_dev._build_user_content("Implement login endpoint")
    assert "RELEVANT HISTORICAL MEMORIES" in content
    assert "fix_pattern" in content
    assert "auth.js" in content
