"""Stack catalog API tests."""

from backend.bootstrap import initialize


def test_stack_catalog_api():
    initialize()
    from backend import state
    from backend.main import app
    from fastapi.testclient import TestClient

    state.PROJECT_BRIEF = "Quest 3 VR Unity game"
    client = TestClient(app)
    resp = client.get("/api/tools/stack-catalog?brief=1")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["stacks"]) >= 5
    vr = next(s for s in data["stacks"] if s["id"] == "vr")
    assert vr.get("matched") is True
    assert "unity_quest_vr.md" in vr.get("recommendedSkills", [])


def test_timeline_api_route():
    initialize()
    from backend.main import app
    from fastapi.testclient import TestClient

    client = TestClient(app)
    resp = client.get("/api/llm-logs/timeline?limit=10")
    assert resp.status_code == 200
    assert "items" in resp.json()
    assert "threads" in resp.json()
