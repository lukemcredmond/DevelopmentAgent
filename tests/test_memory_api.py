"""Project memory API tests."""

from backend.bootstrap import initialize
from fastapi.testclient import TestClient
from backend.main import app
from backend import state
from backend.storage.memory_engine import create_memory_engine


def test_memory_crud_round_trip():
    initialize()
    client = TestClient(app)

    create = client.post(
        "/api/memory",
        json={"content": "Test project fact for agents", "agent": "System", "category": "user_note"},
    )
    assert create.status_code == 200

    listed = client.get("/api/memory?limit=10")
    assert listed.status_code == 200
    entries = listed.json().get("entries", [])
    assert any("Test project fact" in e.get("content", "") for e in entries)

    mem_id = next(e["id"] for e in entries if "Test project fact" in e.get("content", ""))
    patched = client.patch(
        f"/api/memory/{mem_id}",
        json={"content": "Updated project fact for agents", "category": "user_note"},
    )
    assert patched.status_code == 200
    assert patched.json().get("entry", {}).get("content") == "Updated project fact for agents"

    deleted = client.delete(f"/api/memory/{mem_id}")
    assert deleted.status_code == 200

    listed2 = client.get("/api/memory?limit=10")
    assert not any(e["id"] == mem_id for e in listed2.json().get("entries", []))


def test_list_memories_dedupes_identical_content():
    import sqlite3
    import uuid

    from backend.config import DB_PATH

    initialize()
    pid = state.CURRENT_PROJECT_ID or "default-proj"
    scoped = f"{pid}:__project__"
    text = "Duplicate dedupe test content unique xyz"
    mem_id_a = str(uuid.uuid4())
    mem_id_b = str(uuid.uuid4())
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT INTO memories (id, agent_id, category, content) VALUES (?, ?, ?, ?)",
            (mem_id_a, scoped, "user_note", text),
        )
        conn.execute(
            "INSERT INTO memories (id, agent_id, category, content) VALUES (?, ?, ?, ?)",
            (mem_id_b, scoped, "user_note", text),
        )
        conn.commit()

    client = TestClient(app)
    raw = client.get("/api/memory?dedupe=false&limit=50&q=Duplicate+dedupe+test")
    assert raw.status_code == 200
    assert len(raw.json().get("entries", [])) >= 2

    grouped = client.get("/api/memory?dedupe=true&limit=50&q=Duplicate+dedupe+test")
    assert grouped.status_code == 200
    matches = [e for e in grouped.json().get("entries", []) if text in e.get("content", "")]
    assert len(matches) == 1
    assert matches[0].get("duplicateCount", 1) >= 2
    assert len(matches[0].get("duplicateIds", [])) >= 2

    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("DELETE FROM memories WHERE id IN (?, ?)", (mem_id_a, mem_id_b))
        conn.commit()


def test_list_memories_category_and_q_filters():
    initialize()
    pid = state.CURRENT_PROJECT_ID or "default-proj"
    engine = create_memory_engine()
    engine.save("Developer", "filter-alpha dev memory", "fix_pattern", project_id=pid)
    engine.save("__project__", "filter-beta user note", "user_note", project_id=pid)

    client = TestClient(app)
    by_cat = client.get("/api/memory?category=fix_pattern&limit=50&q=filter-alpha")
    assert by_cat.status_code == 200
    entries = by_cat.json().get("entries", [])
    assert entries
    assert all(e.get("category") == "fix_pattern" for e in entries)
    assert any("filter-alpha" in e.get("content", "") for e in entries)

    by_q = client.get("/api/memory?q=filter-beta&limit=50")
    assert by_q.status_code == 200
    assert any("filter-beta" in e.get("content", "") for e in by_q.json().get("entries", []))


def test_save_project_note_skips_exact_duplicate():
    initialize()
    pid = state.CURRENT_PROJECT_ID or "default-proj"
    engine = create_memory_engine()
    note = "Exact duplicate skip test note qwerty"
    engine.save_project_note(note, "user_note", project_id=pid)
    engine.save_project_note(note, "user_note", project_id=pid)

    listed = engine.list_for_project(project_id=pid, q="Exact duplicate skip", dedupe=False, limit=50)
    assert sum(1 for e in listed if e.get("content") == note) == 1


def test_read_file_success_does_not_save_tool_usage_memory():
    initialize()
    from backend.services.tool_execution_service import _record_tool_side_effects

    pid = state.CURRENT_PROJECT_ID or "default-proj"
    engine = create_memory_engine()
    before = len(engine.list_for_project(project_id=pid, dedupe=False, limit=500))

    _record_tool_side_effects(
        task_id="T-1",
        agent_role="Developer",
        tool_name="read_file",
        arguments={"path": "lib/main.dart"},
        safe_args={"path": "lib/main.dart"},
        tool_output="file contents here",
        success=True,
        source="agent",
        save_memory=True,
        user_prompt="implement feature",
        memory_engine=engine,
    )

    after = len(engine.list_for_project(project_id=pid, dedupe=False, limit=500))
    assert after == before
