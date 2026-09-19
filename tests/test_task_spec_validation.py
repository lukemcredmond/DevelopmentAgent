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


def test_prepare_trims_long_description_when_ac_and_test_plan_exist():
    from backend.services.board_service import OVERSIZE_DESC_CHARS, prepare_task_for_dev_claim

    overflow = "extra overflow details for the developer to keep in scope."
    desc = ("Serialize meals to JSON and save. " * 40) + overflow
    assert len(desc) > OVERSIZE_DESC_CHARS
    task = {
        "description": desc,
        "acceptanceCriteria": ["Export from menu", "Happy path test"],
        "scope": "",
        "testPlan": "",
    }
    prepare_task_for_dev_claim(task)
    assert len(task["description"]) <= OVERSIZE_DESC_CHARS
    assert task["testPlan"]
    assert overflow.split()[0] in (task.get("scope") or "") or "serialize" in (task.get("scope") or "").lower()
    assert dev_claim_blocked(task, {"splitCardWhenAcOver": 3}) is None


def test_oversize_description_moves_to_in_progress_after_trim():
    from backend import state
    from backend.agents.task_context import get_task_lane, init_new_task
    from backend.bootstrap import initialize
    from backend.services.board_service import OVERSIZE_DESC_CHARS, move_board_stage

    initialize()
    for lane in ("Backlog", "In Progress", "Needs PO", "Needs User", "QA", "Done"):
        state.SHARED_BOARD.setdefault(lane, [])
        state.SHARED_BOARD[lane] = []
    desc = "Provide an Export action that serializes current data. " * 30
    assert len(desc) > OVERSIZE_DESC_CHARS
    task = init_new_task(
        {
            "id": "T-TRIM",
            "title": "Export core flow",
            "description": desc,
            "status": "Needs PO",
            "acceptanceCriteria": ["Export from menu", "Sample when empty"],
            "workType": "implementation",
            "requiresDev": True,
        }
    )
    state.SHARED_BOARD["Needs PO"] = [task]
    result = move_board_stage("T-TRIM", "In Progress")
    assert get_task_lane("T-TRIM") == "In Progress"
    assert "Could not move" not in result
    assert len(task["description"]) <= OVERSIZE_DESC_CHARS


def test_move_off_needs_po_returns_actual_lane_when_verbs_oversized():
    from backend import state
    from backend.agents.task_context import get_task_lane, init_new_task
    from backend.bootstrap import initialize
    from backend.services.po_clarification import move_off_needs_po

    initialize()
    for lane in ("Backlog", "In Progress", "Needs PO", "Needs User", "QA", "Done", "Refinement"):
        state.SHARED_BOARD.setdefault(lane, [])
        state.SHARED_BOARD[lane] = []
    task = init_new_task(
        {
            "id": "T-VERBS",
            "title": "Export",
            "description": "Implement, add, create, and build the export, then migrate and integrate it.",
            "status": "Needs PO",
            "acceptanceCriteria": ["Export from menu", "Happy path"],
            "scope": "Export",
            "testPlan": "Widget test",
            "workType": "implementation",
            "requiresDev": True,
        }
    )
    state.SHARED_BOARD["Needs PO"] = [task]
    dest = move_off_needs_po("T-VERBS")
    assert dest == "Needs PO"
    assert get_task_lane("T-VERBS") == "Needs PO"


def test_oversize_length_message_tells_po_to_shorten():
    from backend.services.board_service import OVERSIZE_DESC_CHARS, is_oversized_implementation

    desc = "x" * (OVERSIZE_DESC_CHARS + 50)
    reason = is_oversized_implementation(
        {
            "description": desc,
            "acceptanceCriteria": ["Export from menu"],
            "workType": "implementation",
            "requiresDev": True,
        }
    )
    assert reason is not None
    assert str(OVERSIZE_DESC_CHARS) in reason
    assert "shorten" in reason.lower()
