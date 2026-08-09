"""Unit tests for Dev Explore → Patch → Verify phase graph."""

from backend.services.dev_phase_graph import (
    EXPLORE_NUDGE,
    DevPhaseGraph,
    hint_for_exit_reason,
)


def test_starts_in_explore_and_labels_counts():
    g = DevPhaseGraph(explore_max=3, patch_max=4, verify_max=2)
    assert g.phase == "explore"
    assert g.label() == "Explore 0/3"
    assert g.applies_to(role="Developer", lane="In Progress")
    assert not g.applies_to(role="Developer", lane="Refinement")
    assert not g.applies_to(role="QA", lane="In Progress")


def test_explore_tools_count_toward_budget():
    g = DevPhaseGraph(explore_max=3)
    a = g.record_batch([("read_file", True), ("grep", True)])
    assert a.nudge is None
    assert a.stop_reason is None
    assert g.explore_count == 2
    assert g.phase == "explore"


def test_explore_budget_nudges_then_stops_on_more_explore():
    g = DevPhaseGraph(explore_max=2)
    a1 = g.record_batch([("read_file", True), ("list_dir", True)])
    assert a1.nudge == EXPLORE_NUDGE
    assert a1.stop_reason is None
    assert g.explore_nudge_sent

    a2 = g.record_batch([("grep", True)])
    assert a2.stop_reason == "explore_budget_exhausted"
    assert g.phase == "stuck"


def test_explore_nudge_then_llm_turn_without_write_stops():
    g = DevPhaseGraph(explore_max=1)
    g.record_batch([("read_file", True)])
    assert g.pending_stop_after_nudge
    a = g.after_llm_turn_without_write()
    assert a.stop_reason == "explore_budget_exhausted"
    assert g.phase == "stuck"


def test_successful_write_moves_to_verify():
    g = DevPhaseGraph(explore_max=3, verify_max=2)
    g.record_batch([("read_file", True)])
    a = g.record_batch([("apply_patch", True)])
    assert a.stop_reason is None
    assert g.write_succeeded
    assert g.phase == "verify"
    assert g.label() == "Verify 0/2"


def test_failed_write_moves_to_patch_then_budget_stop():
    g = DevPhaseGraph(patch_max=2)
    g.record_batch([("apply_patch", False)])
    assert g.phase == "patch"
    a = g.record_batch([("write_file", False)])
    assert a.stop_reason == "patch_budget_exhausted"
    assert g.phase == "stuck"


def test_verify_tools_only_count_after_write():
    g = DevPhaseGraph(explore_max=5, verify_max=2)
    # run_test before write counts as explore thrash
    g.record_batch([("run_test", False)])
    assert g.explore_count == 1
    assert g.verify_count == 0
    assert g.phase == "explore"

    g.record_batch([("apply_patch", True)])
    assert g.phase == "verify"
    g.record_batch([("run_command", True)])
    assert g.verify_count == 1
    assert g.phase == "verify"
    g.record_batch([("run_test", True)])
    assert g.verify_count == 2
    assert g.phase == "done"


def test_write_after_explore_clears_pending_nudge_stop():
    g = DevPhaseGraph(explore_max=1)
    g.record_batch([("read_file", True)])
    assert g.pending_stop_after_nudge
    g.record_batch([("apply_patch", True)])
    assert not g.pending_stop_after_nudge
    assert g.phase == "verify"
    assert g.after_llm_turn_without_write().stop_reason is None


def test_from_settings_respects_disable_flag():
    assert DevPhaseGraph.from_settings({"enableDevPhaseGraph": False}) is None
    g = DevPhaseGraph.from_settings(
        {
            "enableDevPhaseGraph": True,
            "devExploreMaxTools": 5,
            "devPatchMaxTools": 6,
            "devVerifyMaxTools": 1,
        }
    )
    assert g is not None
    assert g.explore_max == 5
    assert g.patch_max == 6
    assert g.verify_max == 1


def test_hint_for_exit_reason():
    assert "explore budget" in (hint_for_exit_reason("explore_budget_exhausted") or "").lower()
    assert "patch" in (hint_for_exit_reason("patch_budget_exhausted") or "").lower()
    assert hint_for_exit_reason("max_iterations") is None


def test_snapshot_includes_cycle_and_status_text():
    g = DevPhaseGraph(explore_max=3, verify_max=2)
    snap = g.snapshot()
    assert snap["cycle"] == 1
    assert snap["stepLabel"] == "Cycle 1"
    assert snap["priorSummary"] == ""
    assert snap["statusText"]
    assert "Exploring" in snap["statusText"] or "Explore" in snap["statusText"]

    g.record_batch([("apply_patch", True)])
    g.record_batch([("run_test", True), ("run_command", True)])
    assert g.phase == "done"
    done_snap = g.snapshot()
    assert done_snap["phase"] == "done"
    assert "not board Done" in done_snap["statusText"]
    assert done_snap["cycle"] == 1
    assert done_snap["stepLabel"] == "Cycle 1"


def test_for_new_step_seeds_restart_after_done():
    prior = {
        "phase": "done",
        "cycle": 1,
        "label": "Done",
        "exploreCount": 1,
        "exploreMax": 3,
        "patchCount": 1,
        "patchMax": 4,
        "verifyCount": 2,
        "verifyMax": 2,
        "writeSucceeded": True,
    }
    g = DevPhaseGraph.for_new_step(
        {"enableDevPhaseGraph": True, "devExploreMaxTools": 3, "devPatchMaxTools": 4, "devVerifyMaxTools": 2},
        prior_snap=prior,
        steps_on_card=0,
        focus_ac_index=1,
    )
    assert g is not None
    assert g.phase == "explore"
    assert g.cycle >= 2
    snap = g.snapshot()
    assert snap["cycle"] >= 2
    assert snap["stepLabel"] == "Cycle 2 · AC 2"
    assert snap["priorSummary"] == "Verify Done"
    assert "budgets reset" in snap["statusText"].lower()
    assert "Verify Done" in snap["statusText"]
    assert "In Progress" in snap["statusText"]


def test_compute_cycle_from_prior():
    from backend.services.dev_phase_graph import compute_cycle_from_prior

    assert compute_cycle_from_prior() == 1
    assert compute_cycle_from_prior(prior_snap={"phase": "done", "cycle": 1}) == 2
    assert compute_cycle_from_prior(prior_snap={"phase": "stuck"}, steps_on_card=3) == 4
    assert compute_cycle_from_prior(steps_on_card=2) == 3


def test_stuck_sets_status_text():
    g = DevPhaseGraph(explore_max=1)
    g.record_batch([("read_file", True)])
    a = g.record_batch([("grep", True)])
    assert a.stop_reason == "explore_budget_exhausted"
    assert g.phase == "stuck"
    assert "explore tool budget" in g.status_text.lower()
