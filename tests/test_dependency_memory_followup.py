"""Dependency rollup, cross-agent memory, and context summarization."""

from backend.bootstrap import initialize


def test_dependency_outcome_rollup_to_parent_prompt():
    initialize()
    from backend import state
    from backend.agents.task_context import (
        build_task_prompt,
        init_new_task,
        record_task_decision,
    )
    from backend.services.board_service import move_board_stage

    parent = init_new_task({"id": "T-PAR", "title": "Parent feature", "description": "Build auth"})
    child = init_new_task(
        {
            "id": "T-CH",
            "title": "Spike JWT",
            "description": "Research JWT setup",
            "refinementNotes": "Use JwtBearer in Program.cs",
            "parentTaskId": "T-PAR",
        }
    )
    child["blockedBy"] = []
    parent["blockedBy"] = ["T-CH"]
    parent["subtaskIds"] = ["T-CH"]
    state.SHARED_BOARD = _board(**{"In Progress": [parent], "Backlog": [child]})

    record_task_decision("T-CH", "Developer", "spike", "JWT middleware located")
    move_board_stage("T-CH", "Done")

    parent_live = next(t for lane in state.SHARED_BOARD.values() for t in lane if t["id"] == "T-PAR")
    assert any(o.get("taskId") == "T-CH" for o in parent_live.get("dependencyOutcomes") or [])

    prompt = build_task_prompt(parent_live, "API project")
    assert "COMPLETED DEPENDENCY OUTCOMES" in prompt
    assert "JWT middleware located" in prompt or "JwtBearer" in prompt


def test_older_decisions_condensed_in_prompt():
    initialize()
    from backend import state
    from backend.agents.task_context import build_task_prompt, init_new_task, record_task_decision

    task = init_new_task({"id": "T-DEC", "title": "Many decisions", "description": "d"})
    state.SHARED_BOARD = {"Backlog": [task]}
    for i in range(12):
        record_task_decision("T-DEC", "Developer", "note", f"Decision number {i}")

    prompt = build_task_prompt(task, "brief")
    assert "EARLIER DECISIONS (condensed)" in prompt
    assert "Decision number 0" in prompt
    assert "PRIOR AGENT DECISIONS ON THIS CARD" in prompt
    assert "Decision number 11" in prompt


def test_cross_agent_memory_includes_project_notes():
    initialize()
    from backend import state
    from backend.storage.memory_engine import create_memory_engine

    pid = state.CURRENT_PROJECT_ID or "default-proj"
    engine = create_memory_engine()
    engine.save_project_note("API keys live in .env.local", "user_note", project_id=pid)
    hits = engine.search(
        "Developer",
        "where are API keys",
        limit=3,
        project_id=pid,
        include_all_agents=True,
    )
    assert any(".env.local" in h.get("content", "") for h in hits)


def test_validate_blocked_by_missing_ids():
    initialize()
    from backend import state
    from backend.agents.task_context import init_new_task, validate_blocked_by

    task = init_new_task({"id": "T-BLK", "title": "Blocked", "description": "d", "blockedBy": ["T-GONE"]})
    state.SHARED_BOARD = {"Backlog": [task]}
    assert validate_blocked_by(task) == ["T-GONE"]


def _board(**lanes):
    base = {
        "Backlog": [],
        "Refinement": [],
        "In Progress": [],
        "Needs PO": [],
        "Needs User": [],
        "QA": [],
        "Done": [],
    }
    base.update(lanes)
    return base
