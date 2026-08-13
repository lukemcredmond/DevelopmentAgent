"""Spec readiness soft gates for SDD-shaped cards."""

from backend.services.task_spec_validation import dev_claim_blocked, spec_readiness


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


def test_dev_claim_blocks_more_than_three_acceptance_criteria():
    task = {
        "title": "Small implementation",
        "description": "Implement one focused change",
        "workType": "implementation",
        "requiresDev": True,
        "acceptanceCriteria": ["a", "b", "c", "d"],
        "scope": "One component",
        "testPlan": "Run unit tests",
    }
    reason = dev_claim_blocked(task, {"splitCardWhenAcOver": 3})
    assert reason is not None
    assert "4 > 3" in reason


def test_dev_claim_requires_description_scope_and_test_plan():
    task = {
        "title": "Incomplete",
        "description": "",
        "workType": "implementation",
        "requiresDev": True,
        "acceptanceCriteria": ["a"],
        "scope": "",
        "testPlan": "",
    }
    reason = dev_claim_blocked(task, {"splitCardWhenAcOver": 3})
    assert reason is not None
    assert "description" in reason
    assert "scope" in reason
    assert "testPlan" in reason
