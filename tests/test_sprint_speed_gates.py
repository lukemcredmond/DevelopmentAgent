"""Tests for sprint speed gates (unhealthy exits, circuit breaker, interrupt backoff)."""

from __future__ import annotations

from backend.services import sprint_speed_gates as gates


def test_unhealthy_exit_blocks_lane_advance_by_default():
    ws = {"forceCompleteOnUnhealthyExit": False}
    assert gates.unhealthy_exit_blocks_lane_advance("ollama_fallback", ws) is True
    assert gates.unhealthy_exit_blocks_lane_advance("max_iterations", ws) is True
    assert gates.unhealthy_exit_blocks_lane_advance("completed_with_writes", ws) is False


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
    gates.record_consecutive_bad_exit(task, "completed_with_writes")
    assert task.get("consecutiveBadExits") == 0


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
