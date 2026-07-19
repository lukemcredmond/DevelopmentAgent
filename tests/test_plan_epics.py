"""Plan → Features (epics) + child cards and feature rollup."""

import json

from backend import state
from backend.agents.task_context import find_task_by_id, record_task_decision, record_task_file
from backend.bootstrap import initialize
from backend.services.board_lanes import FEATURES_LANE
from backend.services.feature_service import (
    apply_plan_epics_from_po_output,
    build_feature_rollup,
    list_features,
    rollup_child_to_feature,
)
from backend.services.sprint_service import _append_po_backlog_from_output


def _clear_board():
    state.SHARED_BOARD.clear()
    for lane in (
        FEATURES_LANE,
        "Backlog",
        "Pending Approval",
        "Refinement",
        "In Progress",
        "Needs User",
        "Needs PO",
        "Code Review",
        "QA",
        "Done",
    ):
        state.SHARED_BOARD[lane] = []


def _clear_logs():
    state.SYSTEM_LOGS.clear()


def test_apply_plan_epics_json_creates_features_and_children():
    initialize()
    _clear_board()
    po_output = """
    {
      "epics": [
        {
          "title": "Auth epic",
          "description": "User authentication",
          "children": [
            {
              "title": "Login form",
              "description": "Email password login UI",
              "acceptanceCriteria": ["Form validates"]
            },
            {
              "title": "Session token",
              "description": "Issue JWT on login",
              "acceptanceCriteria": ["Token returned"]
            }
          ]
        }
      ]
    }
    """
    result = apply_plan_epics_from_po_output(po_output)
    assert result["epicCount"] == 1
    assert result["childCount"] == 2
    features = list_features()
    assert len(features) == 1
    feat = features[0]
    assert feat["title"] == "Auth epic"
    assert feat["workType"] == "feature"
    assert len(feat.get("childTaskIds") or []) == 2
    for cid in feat["childTaskIds"]:
        child = find_task_by_id(cid)
        assert child is not None
        assert child.get("featureId") == feat["id"]


def test_legacy_flat_array_becomes_synthetic_epic():
    initialize()
    _clear_board()
    po_output = """
    [
      {"title": "Scaffold app", "description": "Create entry point", "acceptanceCriteria": ["Runs"]},
      {"title": "Add README", "description": "Document setup", "acceptanceCriteria": ["README present"]}
    ]
    """
    result = apply_plan_epics_from_po_output(po_output)
    assert result["epicCount"] == 1
    assert result["childCount"] == 2
    features = list_features()
    assert len(features) == 1
    assert features[0]["title"] == "Project backlog"


def test_simulation_fallback_creates_two_epics():
    initialize()
    _clear_board()
    result = apply_plan_epics_from_po_output("SIMULATION_FALLBACK")
    assert result["epicCount"] == 2
    assert result["childCount"] == 2
    assert len(list_features()) == 2


def test_append_po_backlog_uses_epics():
    initialize()
    _clear_board()
    count = _append_po_backlog_from_output(
        '{"epics":[{"title":"Billing","description":"Payments","children":[{"title":"Invoice PDF","description":"Generate PDF","acceptanceCriteria":["PDF downloads"]}]}]}',
        set(),
    )
    assert count == 1
    assert len(list_features()) == 1


def test_under_decomposition_warns_for_few_epics_long_brief():
    initialize()
    _clear_board()
    _clear_logs()
    state.PROJECT_BRIEF = "x" * 450
    state.PROJECT_PLAN_OUTLINE = ""
    result = apply_plan_epics_from_po_output(
        '{"epics":[{"title":"Audit","description":"Fix all","children":[{"title":"Fix compile","description":"All errors","acceptanceCriteria":["Builds"]}]}]}'
    )
    assert result["epicCount"] == 1
    warnings = [e for e in state.SYSTEM_LOGS if e.get("type") == "warning"]
    assert any("Under-decomposed" in (e.get("text") or "") for e in warnings)


def test_dependency_only_single_child_warns_but_creates():
    initialize()
    _clear_board()
    _clear_logs()
    state.PROJECT_BRIEF = "short"
    state.PROJECT_PLAN_OUTLINE = ""
    result = apply_plan_epics_from_po_output(
        '{"epics":[{"title":"Deps","description":"Bump packages","children":[{"title":"Update pubspec dependencies","description":"Bump flutter deps","acceptanceCriteria":["pubspec resolves"]}]}]}'
    )
    assert result["epicCount"] == 1
    assert result["childCount"] == 1
    warnings = [e for e in state.SYSTEM_LOGS if e.get("type") == "warning"]
    assert any("dependency-only" in (e.get("text") or "").lower() for e in warnings)
    assert len(list_features()) == 1


def test_multi_epic_payload_accepted_without_under_decomp_warning():
    initialize()
    _clear_board()
    _clear_logs()
    state.PROJECT_BRIEF = "x" * 450
    epics = []
    for i in range(6):
        epics.append(
            {
                "title": f"Epic {i}",
                "description": f"Capability {i}",
                "children": [
                    {"title": f"Child {i}a", "description": "a", "acceptanceCriteria": ["ok"]},
                    {"title": f"Child {i}b", "description": "b", "acceptanceCriteria": ["ok"]},
                ],
            }
        )
    result = apply_plan_epics_from_po_output(json.dumps({"epics": epics}))
    assert result["epicCount"] == 6
    assert result["childCount"] == 12
    warnings = [e for e in state.SYSTEM_LOGS if e.get("type") == "warning"]
    assert not any("Under-decomposed" in (e.get("text") or "") for e in warnings)


def test_feature_rollup_includes_child_files_and_decisions():
    initialize()
    _clear_board()
    result = apply_plan_epics_from_po_output(
        '{"epics":[{"title":"Search","description":"Product search","children":[{"title":"Index rebuild","description":"Rebuild search index","acceptanceCriteria":["Index fresh"]}]}]}'
    )
    feature_id = result["epicIds"][0]
    child_id = result["childIds"][0]
    child = find_task_by_id(child_id)
    assert child is not None
    record_task_file(child_id, "services/search.py", "updated")
    record_task_decision(child_id, "Developer", "implement", "Rebuilt search index")
    # Move child to Done and roll up
    for lane in list(state.SHARED_BOARD.keys()):
        state.SHARED_BOARD[lane] = [t for t in state.SHARED_BOARD[lane] if t.get("id") != child_id]
    child["status"] = "Done"
    state.SHARED_BOARD.setdefault("Done", []).append(child)
    rollup_child_to_feature(child_id)

    feature = find_task_by_id(feature_id)
    assert feature is not None
    paths = []
    for f in feature.get("files") or []:
        paths.append(f.get("path") if isinstance(f, dict) else str(f))
    assert "services/search.py" in paths

    rollup = build_feature_rollup(feature_id)
    assert any(c["id"] == child_id for c in rollup["children"])
    assert "services/search.py" in rollup["files"]
    assert any("search" in (d.get("summary") or "").lower() for d in rollup["recentDecisions"])


def test_build_state_attaches_feature_rollup():
    initialize()
    _clear_board()
    apply_plan_epics_from_po_output(
        '{"epics":[{"title":"UI","description":"Theme","children":[{"title":"Dark mode","description":"Toggle","acceptanceCriteria":["Persists"]}]}]}'
    )
    from backend.api.helpers import build_state_response

    data = build_state_response(include_files=False)
    features = data["board"].get(FEATURES_LANE) or []
    assert features
    assert features[0].get("featureRollup") is not None
    assert features[0]["featureRollup"]["children"]
