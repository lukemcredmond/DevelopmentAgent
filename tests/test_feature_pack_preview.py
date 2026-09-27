"""Feature pack preview and approve."""

from backend.bootstrap import initialize
from backend.services.feature_pack_service import (
    approve_pending_feature_pack,
    build_feature_pack_preview,
    set_pending_feature_pack,
)
from backend.services.plan_outline_quality import sample_v2_plan_outline
from backend.services.workflow_settings import save_workflow_settings


def test_build_feature_pack_preview_from_outline_fallback():
    initialize()
    save_workflow_settings({"requireFeaturePackApproval": True})
    from backend.services.feature_service import build_epics_json_from_plan_outline

    outline = sample_v2_plan_outline()
    epics_json = build_epics_json_from_plan_outline(outline)
    preview = build_feature_pack_preview(epics_json, outline_text=outline, source="fallback")
    assert preview["stats"]["epicCount"] >= 6
    assert preview["stats"]["childCount"] >= 6
    set_pending_feature_pack(preview)


def test_approve_materializes_features():
    initialize()
    save_workflow_settings({"requireFeaturePackApproval": False})
    from backend import state
    from backend.services.board_lanes import FEATURES_LANE
    from backend.services.feature_service import build_epics_json_from_plan_outline, list_features

    state.SHARED_BOARD[FEATURES_LANE] = []
    outline = sample_v2_plan_outline()
    epics_json = build_epics_json_from_plan_outline(outline)
    preview = build_feature_pack_preview(epics_json, outline_text=outline)
    set_pending_feature_pack(preview)
    result = approve_pending_feature_pack()
    assert result.get("ok")
    assert len(list_features()) >= 1
