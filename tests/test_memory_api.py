"""Project memory API tests."""

from backend.bootstrap import initialize
from fastapi.testclient import TestClient
from backend.main import app


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
