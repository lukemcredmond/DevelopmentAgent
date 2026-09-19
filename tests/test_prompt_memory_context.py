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


def test_build_user_content_drops_noisy_xml_and_closed_client_memories():
    initialize()
    from backend.agents.registry import agent_po

    agent_po.memory.search = lambda role, query, limit=3, project_id=None, **kwargs: [
        {
            "category": "fix_pattern",
            "content": "Step stop=llm_call_failed; LLM_CALL_FAILED: Cannot send a request, as the client has been closed.",
        },
        {
            "category": "fix_pattern",
            "content": "Step stop=completed_text_only; <tool_call><function=list_dir>",
        },
        {"category": "fix_pattern", "content": "Prefer small epics with testable AC."},
    ]
    with patch("backend.services.prompt_profile.is_local_slm_profile", return_value=False):
        content = agent_po._build_user_content("Produce a markdown plan")
    assert "client has been closed" not in content
    assert "<tool_call>" not in content
    assert "Prefer small epics" in content


def test_save_step_lesson_skips_planning_task(monkeypatch):
    initialize()
    from backend import state
    from backend.agents.registry import agent_po

    saved = []
    monkeypatch.setattr(
        agent_po.memory,
        "save_step_lesson",
        lambda *args, **kwargs: saved.append(kwargs or args),
    )
    state.ACTIVE_SPRINT_TASK_ID = "PLANNING"
    try:
        agent_po._save_step_lesson("completed_text_only", set(), "<tool_call>")
    finally:
        state.ACTIVE_SPRINT_TASK_ID = None
    assert saved == []
