"""Spec readiness soft gates for SDD-shaped cards."""

from backend.services.task_spec_validation import spec_readiness


def test_spec_readiness_fails_without_ac():
    task = {
        "title": "Add login",
        "description": "Wire OAuth",
        "workType": "implementation",
        "acceptanceCriteria": [],
    }
    result = spec_readiness(task)
    assert result["ok"] is False
    assert any("acceptanceCriteria" in m for m in result["missing"])


def test_spec_readiness_passes_minimal_implementation():
    task = {
        "title": "Add login",
        "description": "Wire OAuth",
        "workType": "implementation",
        "acceptanceCriteria": ["User can sign in", "Token stored securely"],
        "userStory": "",
        "testPlan": "",
        "scope": "",
    }
    result = spec_readiness(task)
    assert result["ok"] is True
    assert result["missing"] == []
    assert len(result["warnings"]) >= 1


def test_spec_readiness_spike_allows_no_ac():
    task = {
        "title": "Spike auth libs",
        "description": "Compare options",
        "workType": "spike",
        "acceptanceCriteria": [],
    }
    result = spec_readiness(task)
    assert result["ok"] is True
