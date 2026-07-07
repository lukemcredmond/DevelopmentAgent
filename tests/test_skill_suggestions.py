"""Brief category extraction and role-aware skill suggestions."""

from backend.bootstrap import initialize
from backend.services.skill_suggestions import (
    brief_categories_for_ui,
    build_suggestions_response,
    extract_brief_categories,
    score_skills_for_agent,
)


def test_extract_categories_aspnet_web():
    cats = extract_brief_categories("ASP.NET Core 8 Web API for customer management")
    assert "csharp" in cats
    assert "web" in cats
    assert "product" in cats


def test_extract_categories_quest_vr():
    cats = extract_brief_categories("Quest 3 VR game in Unity with hand tracking")
    assert "vr" in cats
    assert "product" in cats


def test_brief_categories_for_ui_labels():
    ui = brief_categories_for_ui("Build a Flutter mobile app")
    ids = {c["id"] for c in ui}
    labels = {c["label"] for c in ui}
    assert "flutter" in ids
    assert "Flutter" in labels


def test_po_suggestions_favor_product_skills():
    initialize()
    brief = "ASP.NET Core 8 Web API"
    suggestions = score_skills_for_agent("po", brief=brief, assigned=[], limit=5)
    filenames = [s["filename"] for s in suggestions]
    assert "product_owner.md" in filenames
    top = filenames[0]
    assert top in ("product_owner.md", "acceptance_tester.md")


def test_dev_suggestions_favor_stack_skills():
    initialize()
    brief = "Quest 3 VR game in Unity"
    suggestions = score_skills_for_agent("dev", brief=brief, assigned=[], limit=5)
    filenames = [s["filename"] for s in suggestions]
    assert "unity_quest_vr.md" in filenames


def test_assigned_skills_excluded():
    initialize()
    brief = "ASP.NET Core Web API"
    suggestions = score_skills_for_agent(
        "dev",
        brief=brief,
        assigned=["csharp_api.md"],
        limit=10,
    )
    assert not any(s["filename"] == "csharp_api.md" for s in suggestions)


def test_suggestions_api_shape():
    initialize()
    from backend import state

    state.PROJECT_BRIEF = "Python FastAPI backend"
    resp = build_suggestions_response("qa", limit=3)
    assert "briefCategories" in resp
    assert "suggestions" in resp
    assert any(c["id"] == "python" for c in resp["briefCategories"])


def test_suggestions_api_route():
    initialize()
    from backend import state
    from backend.main import app
    from fastapi.testclient import TestClient

    state.PROJECT_BRIEF = "C# console application"
    client = TestClient(app)
    resp = client.get("/api/skills/suggestions?agent=po&limit=3")
    assert resp.status_code == 200
    data = resp.json()
    assert "briefCategories" in data
    assert "suggestions" in data
