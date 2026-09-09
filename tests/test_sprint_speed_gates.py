"""Tests for sprint speed gates (unhealthy exits, circuit breaker, interrupt backoff)."""

from __future__ import annotations

from backend.services import sprint_speed_gates as gates


def test_unhealthy_exit_blocks_lane_advance_by_default():
    ws = {"forceCompleteOnUnhealthyExit": False}
    assert gates.unhealthy_exit_blocks_lane_advance("ollama_fallback", ws) is True
    assert gates.unhealthy_exit_blocks_lane_advance("max_iterations", ws) is True
    assert gates.unhealthy_exit_blocks_lane_advance("max_iterations_after_writes", ws) is True
    assert gates.unhealthy_exit_blocks_lane_advance("completed_with_writes", ws) is False
    assert (
        gates.unhealthy_exit_blocks_lane_advance(
            "max_iterations_after_writes",
            ws,
            writes_succeeded=1,
            lint_clean=True,
        )
        is False
    )
    assert (
        gates.unhealthy_exit_blocks_lane_advance(
            "max_iterations_after_writes",
            ws,
            writes_succeeded=1,
            lint_clean=False,
        )
        is True
    )


def test_force_complete_overrides_unhealthy_gate():
    ws = {"forceCompleteOnUnhealthyExit": True}
    assert gates.unhealthy_exit_blocks_lane_advance("ollama_fallback", ws) is False


def test_circuit_breaker_trips_on_consecutive_bad_exits():
    task: dict = {}
    ws = {
        "enableStuckCircuitBreaker": True,
        "circuitBreakerMaxBadExits": 3,
        "circuitBreakerIdenticalPatchFails": 9,
    }
    assert gates.circuit_breaker_should_trip(task, ws)[0] is False
    for _ in range(3):
        gates.record_consecutive_bad_exit(task, "max_iterations")
    trip, reason = gates.circuit_breaker_should_trip(task, ws)
    assert trip is True
    assert "consecutive" in reason.lower()


def test_circuit_breaker_trips_on_identical_patches():
    task: dict = {}
    ws = {
        "enableStuckCircuitBreaker": True,
        "circuitBreakerMaxBadExits": 99,
        "circuitBreakerIdenticalPatchFails": 3,
    }
    fp = gates.patch_fingerprint("lib/a.dart", old_text="x", summary="y")
    for _ in range(3):
        gates.record_failed_patch_fingerprint(task, fp)
    trip, reason = gates.circuit_breaker_should_trip(task, ws)
    assert trip is True
    assert "apply_patch" in reason.lower()


def test_healthy_exit_clears_bad_streak():
    task = {"consecutiveBadExits": 2, "lastCircuitExitReason": "max_iterations"}
    gates.record_consecutive_bad_exit(task, "completed_with_writes", progress_made=True)
    assert task.get("consecutiveBadExits") == 0


def test_partial_writes_without_lane_progress_keep_bad_streak():
    task = {"consecutiveBadExits": 1, "lastCircuitExitReason": "max_iterations"}
    gates.record_consecutive_bad_exit(task, "completed_with_writes", progress_made=False)
    assert task["consecutiveBadExits"] == 2
    assert task["lastCircuitExitReason"] == "completed_with_writes_no_advance"


def test_needs_po_skip_after_circuit_and_latch():
    task: dict = {}
    ws = {
        "enableStuckCircuitBreaker": True,
        "circuitBreakerMaxBadExits": 3,
        "circuitBreakerIdenticalPatchFails": 9,
    }
    assert gates.needs_po_should_skip_auto(task, ws) is False
    for _ in range(3):
        gates.record_consecutive_bad_exit(task, "po_clarification_incomplete")
    assert gates.needs_po_should_skip_auto(task, ws) is True
    fresh: dict = {}
    gates.latch_needs_po_auto_skip(fresh, reason="empty PO")
    assert gates.needs_po_should_skip_auto(fresh, ws) is True


def test_stuck_is_explore_without_write():
    assert gates.stuck_is_explore_without_write(
        {"lastStepOutcome": {"exitReason": "explore_budget_exhausted"}}
    )
    assert gates.should_force_patch_next_dev_step({"forcePatchNextDevStep": True})
    assert not gates.stuck_is_explore_without_write(
        {"lastStepOutcome": {"exitReason": "po_clarified"}}
    )


def test_force_patch_after_write_exits():
    assert gates.should_force_patch_next_dev_step(
        {"lastStepOutcome": {"exitReason": "max_iterations_after_writes"}}
    )
    assert gates.should_force_patch_next_dev_step(
        {"lastStepOutcome": {"exitReason": "completed_with_writes"}}
    )
    assert gates.should_force_patch_next_dev_step(
        {"lastStepOutcome": {"exitReason": "identical_write_loop"}}
    )
    assert not gates.should_force_patch_next_dev_step(
        {"lastStepOutcome": {"exitReason": "max_iterations"}}
    )


def test_identical_success_write_trips_at_three():
    task: dict = {}
    fp = gates.successful_write_fingerprint(
        "lib/store_list_screen.dart", summary="replace 645 chars"
    )
    assert gates.record_successful_write_fingerprint(task, fp) == 1
    assert gates.record_successful_write_fingerprint(task, fp) == 2
    assert gates.identical_write_loop_reached(task, ws={"identicalSuccessWriteLimit": 3}) is False
    assert gates.record_successful_write_fingerprint(task, fp) == 3
    assert gates.identical_write_loop_reached(task, ws={"identicalSuccessWriteLimit": 3}) is True
    other = gates.successful_write_fingerprint("lib/other.dart", summary="replace 10 chars")
    assert gates.record_successful_write_fingerprint(task, other) == 1
    assert gates.identical_write_loop_reached(task, ws={"identicalSuccessWriteLimit": 3}) is False


def test_successful_write_fingerprint_uses_path_and_replace_size_not_text():
    path = "mealplanner/lib/presentation/store_list_screen.dart"
    a = gates.successful_write_fingerprint(
        path,
        summary="replace 107 chars",
        old_text="old-a",
        new_text="new-aaaaaaaa",
    )
    b = gates.successful_write_fingerprint(
        path,
        summary="replace 107 chars",
        old_text="old-b-completely-different",
        new_text="new-bbbbbbbb",
    )
    assert a == b
    different_size = gates.successful_write_fingerprint(
        path, summary="replace 645 chars", old_text="x", new_text="y"
    )
    assert a != different_size
    from_len = gates.successful_write_fingerprint(path, old_text="", new_text="x" * 107)
    assert from_len == a


def test_needs_po_skip_when_phase_cycle_cap_reached():
    assert gates.needs_po_should_skip_auto({"phaseCycleCapReached": True}) is True


def test_early_interrupt_backoff_escalates():
    gates.reset_interrupt_backoff_state()
    ws = {
        "enableAutoSprintInterruptBackoff": True,
        "autoSprintInterruptBackoffSec": 2,
        "autoSprintInterruptBackoffMaxSec": 30,
        "interruptEarlyMaxMs": 30000,
    }
    d1 = gates.note_early_interrupt(
        exit_reason="interrupted", ollama_call_count=0, duration_ms=5000, ws=ws
    )
    d2 = gates.note_early_interrupt(
        exit_reason="interrupted", ollama_call_count=0, duration_ms=5000, ws=ws
    )
    assert d1 == 2
    assert d2 == 4
    # Successful / non-early clears streak
    d3 = gates.note_early_interrupt(
        exit_reason="completed_with_writes", ollama_call_count=3, duration_ms=60000, ws=ws
    )
    assert d3 == 0


def test_phase_cycle_cap():
    ws = {"maxDevPhaseCyclesPerCard": 12}
    assert gates.phase_cycle_cap_reached(12, ws) is False
    assert gates.phase_cycle_cap_reached(13, ws) is True


def test_dev_visit_13_latches_and_subsequent_pick_does_not_increment():
    task = {"devStepCount": 12}
    ws = {"maxDevStepsPerCard": 12}
    visit, capped = gates.begin_dev_step(task, ws)
    assert (visit, capped) == (13, True)
    assert task["phaseCycleCapReached"] is True
    assert task["phaseCycleCapAt"] == 13

    second_visit, second_capped = gates.begin_dev_step(task, ws)
    assert (second_visit, second_capped) == (13, True)


def test_three_identical_zero_work_exits_trip_watchdog():
    watchdog = {}
    ws = {"enableZeroWorkRetryWatchdog": True, "zeroWorkRetryWatchdogMax": 3}
    for _ in range(2):
        assert not gates.note_zero_work_exit(
            watchdog,
            task_id="T1",
            exit_reason="phase_cycle_cap",
            ollama_call_count=0,
            tool_call_count=0,
            ws=ws,
        )
    assert gates.note_zero_work_exit(
        watchdog,
        task_id="T1",
        exit_reason="phase_cycle_cap",
        ollama_call_count=0,
        tool_call_count=0,
        ws=ws,
    )


def test_no_write_stall_parks_after_two_explore_exhausts():
    task: dict = {}
    ws = {"maxConsecutiveNoWriteStall": 2}
    gates.record_consecutive_bad_exit(task, "explore_budget_exhausted", writes_succeeded=0)
    assert gates.no_write_stall_should_park(task, ws) is False
    gates.record_consecutive_bad_exit(task, "explore_budget_exhausted", writes_succeeded=0)
    assert gates.no_write_stall_should_park(task, ws) is True
    assert task["consecutiveNoWriteStall"] == 2


def test_duplicate_tool_with_writes_does_not_reset_stall():
    task: dict = {}
    ws = {"maxConsecutiveNoWriteStall": 2}
    gates.record_consecutive_bad_exit(task, "duplicate_tool", writes_succeeded=0)
    gates.record_consecutive_bad_exit(task, "duplicate_tool", writes_succeeded=2)
    assert task.get("consecutiveNoWriteStall") == 1
    assert gates.no_write_stall_should_park(task, ws) is False


def test_duplicate_tool_without_writes_stalls():
    task: dict = {}
    ws = {"maxConsecutiveNoWriteStall": 2}
    gates.record_consecutive_bad_exit(task, "duplicate_tool", writes_succeeded=0)
    gates.record_consecutive_bad_exit(task, "duplicate_tool", writes_succeeded=0)
    assert gates.no_write_stall_should_park(task, ws) is True


def test_write_cap_without_lane_advance_counts_toward_stall():
    task: dict = {}
    ws = {"maxConsecutiveNoWriteStall": 2}
    gates.record_consecutive_bad_exit(task, "explore_budget_exhausted", writes_succeeded=0)
    gates.record_consecutive_bad_exit(
        task, "max_iterations_after_writes", writes_succeeded=2, progress_made=False
    )
    assert task["consecutiveNoWriteStall"] == 2
    assert gates.no_write_stall_should_park(task, ws) is True


def test_identical_write_loop_counts_toward_stall():
    task: dict = {}
    ws = {"maxConsecutiveNoWriteStall": 2}
    gates.record_consecutive_bad_exit(
        task, "identical_write_loop", writes_succeeded=1, progress_made=False
    )
    gates.record_consecutive_bad_exit(
        task, "identical_write_loop", writes_succeeded=1, progress_made=False
    )
    assert gates.no_write_stall_should_park(task, ws) is True


def test_write_that_advances_lane_resets_stall():
    task: dict = {}
    ws = {"maxConsecutiveNoWriteStall": 2}
    gates.record_consecutive_bad_exit(task, "explore_budget_exhausted", writes_succeeded=0)
    gates.record_consecutive_bad_exit(
        task, "completed_with_writes", writes_succeeded=1, progress_made=True
    )
    assert task.get("consecutiveNoWriteStall") == 0
    assert gates.no_write_stall_should_park(task, ws) is False
