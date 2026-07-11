"""Tests for Features lane, feature_service, and PO feature intake."""

from unittest.mock import patch

from backend import state
from backend.agents.task_context import init_new_task, on_task_completed
from backend.bootstrap import initialize
from backend.services.board_lanes import FEATURES_LANE, normalize_board_lanes
from backend.services.board_service import move_board_stage
from backend.services.feature_service import (
    build_feature_context_block,
    create_feature,
    find_feature_by_id,
    intake_feature_offline,
    list_features,
    rollup_child_to_feature,
    update_feature,
)
from backend.services.sprint_service import run_po_add_feature
from backend.services.workflow_settings import get_active_lanes


def _reset_board() -> None:
    state.SHARED_BOARD.clear()
    normalize_board_lanes(state.SHARED_BOARD)


def test_active_lanes_includes_features_first():
    initialize()
    lanes = get_active_lanes()
    assert lanes[0] == "Features"
    assert "Backlog" in lanes


def test_create_feature_spawns_child_with_feature_id():
    initialize()
    _reset_board()
    feature, child = create_feature(
        "User Auth",
        "Login and logout flows",
        request_title="Add auth",
        request_body="Users need login",
        child_task={
            "title": "Implement login",
            "description": "Email/password login",
            "acceptanceCriteria": ["User can log in"],
        },
    )
    assert feature["workType"] == "feature"
    assert str(feature["id"]).startswith("FEAT-")
    assert feature["status"] == FEATURES_LANE
    assert feature["id"] in [t["id"] for t in state.SHARED_BOARD[FEATURES_LANE]]
    assert child.get("featureId") == feature["id"]
    assert child["id"] in (feature.get("childTaskIds") or [])
    backlog = state.SHARED_BOARD.get("Backlog") or state.SHARED_BOARD.get("Refinement") or []
    assert any(t["id"] == child["id"] for t in backlog)


def test_feature_card_cannot_move_out_of_features_lane():
    initialize()
    _reset_board()
    feature, _ = create_feature(
        "Payments",
        "Stripe integration",
        request_title="Payments",
        request_body="Add Stripe",
        child_task={"title": "Stripe setup", "description": "d", "acceptanceCriteria": ["Works"]},
    )
    result = move_board_stage(feature["id"], "Backlog")
    assert result.startswith("Error")
    assert feature["id"] in [t["id"] for t in state.SHARED_BOARD[FEATURES_LANE]]


def test_update_feature_spawns_second_child():
    initialize()
    _reset_board()
    feature, child1 = create_feature(
        "Search",
        "Full-text search",
        request_title="Search v1",
        request_body="Basic search",
        child_task={"title": "Search index", "description": "d", "acceptanceCriteria": ["Index works"]},
    )
    feature2, child2 = update_feature(
        feature["id"],
        title="Search",
        description="Full-text search with filters",
        request_title="Search v2",
        request_body="Add filters",
        child_task={"title": "Filter UI", "description": "d", "acceptanceCriteria": ["Filters work"]},
        po_summary="Added filter requirement",
    )
    assert feature2["id"] == feature["id"]
    assert len(feature2.get("childTaskIds") or []) == 2
    assert child1["id"] in feature2["childTaskIds"]
    assert child2["id"] in feature2["childTaskIds"]
    history = feature2.get("featureHistory") or []
    assert len(history) >= 2


def test_rollup_child_to_feature_on_done():
    initialize()
    _reset_board()
    feature, child = create_feature(
        "Notifications",
        "Push notifications",
        request_title="Notify",
        request_body="Push alerts",
        child_task={"title": "FCM setup", "description": "d", "acceptanceCriteria": ["Push works"]},
    )
    child_id = child["id"]
    for lane in list(state.SHARED_BOARD.keys()):
        state.SHARED_BOARD[lane] = [t for t in state.SHARED_BOARD[lane] if t.get("id") != child_id]
    child_task = find_feature_by_id(feature["id"])
    assert child_task is not None
    completed = init_new_task(
        {
            "id": child_id,
            "title": "FCM setup",
            "description": "d",
            "status": "Done",
            "featureId": feature["id"],
        }
    )
    completed["decisions"] = [
        {"timestamp": "2026-01-01", "agent": "QA", "type": "qa", "summary": "All tests passed"}
    ]
    state.SHARED_BOARD.setdefault("Done", []).append(completed)
    on_task_completed(child_id)
    updated = find_feature_by_id(feature["id"])
    assert updated is not None
    history = updated.get("featureHistory") or []
    assert any(h.get("source") == "rollup" for h in history if isinstance(h, dict))


def test_build_task_prompt_includes_feature_context():
    initialize()
    _reset_board()
    from backend.agents.task_context import build_task_prompt

    feature, child = create_feature(
        "Profile",
        "User profile page",
        request_title="Profile",
        request_body="Edit profile",
        child_task={"title": "Profile form", "description": "d", "acceptanceCriteria": ["Form saves"]},
        po_summary="Initial profile scope",
    )
    child_full = None
    for lane_tasks in state.SHARED_BOARD.values():
        for t in lane_tasks:
            if t.get("id") == child.get("id"):
                child_full = t
                break
    assert child_full is not None
    prompt = build_task_prompt(child_full, "Brief text")
    assert f"FEATURE CONTEXT (parent {feature['id']})" in prompt
    assert "Living spec" in prompt


def test_build_feature_context_block():
    initialize()
    _reset_board()
    feature, _ = create_feature(
        "API",
        "REST API",
        request_title="API",
        request_body="CRUD endpoints",
        child_task={"title": "Users API", "description": "d", "acceptanceCriteria": ["CRUD"]},
        po_summary="REST layer",
    )
    block = build_feature_context_block(feature["id"])
    assert "FEATURE CONTEXT" in block
    assert "REST API" in block


def test_run_po_add_feature_simulation_fallback_creates_feature_structure():
    initialize()
    _reset_board()
    with patch("backend.services.sprint_service.agent_po") as mock_po:
        mock_po.execute_step.return_value = "SIMULATION_FALLBACK"
        run_po_add_feature("Offline feature", "Test offline intake", "http://localhost:11434")
    features = list_features()
    assert len(features) == 1
    assert features[0]["workType"] == "feature"
    child_ids = features[0].get("childTaskIds") or []
    assert len(child_ids) == 1


def test_run_po_add_feature_update_existing():
    initialize()
    _reset_board()
    feature, _ = create_feature(
        "Cart",
        "Shopping cart",
        request_title="Cart v1",
        request_body="Basic cart",
        child_task={"title": "Add to cart", "description": "d", "acceptanceCriteria": ["Adds item"]},
    )
    po_json = (
        '{"action":"update","featureId":"'
        + feature["id"]
        + '","featureTitle":"Shopping cart","featureDescription":"Cart with coupons",'
        '"historySummary":"Add coupon support","childTask":{"title":"Coupon field",'
        '"description":"Apply coupons","acceptanceCriteria":["Coupon applies"]}}'
    )
    with patch("backend.services.sprint_service.agent_po") as mock_po:
        mock_po.execute_step.return_value = po_json
        run_po_add_feature("Coupons", "Users can apply discount codes", "http://localhost:11434")
    updated = find_feature_by_id(feature["id"])
    assert updated is not None
    assert "coupon" in (updated.get("description") or "").lower() or len(updated.get("childTaskIds") or []) >= 2


def test_intake_feature_offline():
    initialize()
    _reset_board()
    feature, child = intake_feature_offline("Widget", "Dashboard widget")
    assert feature["workType"] == "feature"
    assert child.get("featureId") == feature["id"]
