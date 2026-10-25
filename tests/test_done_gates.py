"""Done gates under implementer relaxation (AC checklist, analyze clean)."""

from __future__ import annotations

import pytest

from backend import state


@pytest.fixture
def implementer_ws(monkeypatch):
    monkeypatch.setattr(
        "backend.services.workflow_settings.get_workflow_settings",
        lambda: {
            "executionProfile": "implementer",
            "implementerRelaxGatesAlways": True,
            "implementerRelaxGatesOnAutoSprint": True,
            "implementerRequireAcChecklistForDone": True,
            "implementerRequireAnalyzeClean": True,
            "requireAcChecklistForDone": True,
        },
    )


def test_dev_gate_blocks_done_unchecked_ac(implementer_ws):
    from backend.services.sprint_service import dev_gate_blocks_advance

    task = {
        "id": "T1",
        "acceptanceCriteria": ["A", "B"],
        "acChecklist": [True, False],
        "files": [{"path": "lib/x.dart", "action": "write"}],
    }
    blocked, reason = dev_gate_blocks_advance(task, advance_target="Done")
    assert blocked
    assert "Acceptance criteria unchecked" in reason


def test_dev_gate_allows_qa_without_ac_when_relaxed(implementer_ws):
    from backend.services.sprint_service import dev_gate_blocks_advance

    task = {
        "id": "T1",
        "acceptanceCriteria": ["A"],
        "acChecklist": [False],
        "files": [{"path": "lib/x.dart", "action": "write"}],
    }
    blocked, _ = dev_gate_blocks_advance(task, advance_target="QA")
    assert not blocked


def test_dev_gate_blocks_done_workspace_analyze_dirty(implementer_ws, monkeypatch):
    from backend.services.sprint_service import dev_gate_blocks_advance

    state.WORKSPACE_ANALYZE_CLEAN = False
    state.WORKSPACE_ANALYZE_DETAIL = "errors"
    task = {
        "id": "T1",
        "acceptanceCriteria": ["A"],
        "acChecklist": [True],
        "files": [{"path": "lib/x.dart", "action": "write"}],
    }
    blocked, reason = dev_gate_blocks_advance(task, advance_target="Done")
    assert blocked
    assert "analyze" in reason.lower()
