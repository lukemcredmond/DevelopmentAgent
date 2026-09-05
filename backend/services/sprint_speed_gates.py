"""Sprint speed gates: unhealthy exit blocking, stuck circuit breakers, interrupt backoff."""

from __future__ import annotations

import hashlib
import time
from typing import Any, Dict, Optional, Set

# Exits that must not promote In Progress → QA / Code Review.
UNHEALTHY_LANE_ADVANCE_EXITS = frozenset(
    {
        "phase_cycle_cap",
        "ollama_fallback",
        "tool_failure_stop",
        "interrupted",
        "max_iterations",
        "plan_exhausted",
        "read_only_no_edits",
        "command_repeat_no_progress",
        "duplicate_tool",
        "step_timeout",
        "explore_budget_exhausted",
        "patch_budget_exhausted",
        "completed_text_only",
        "completed_with_writes_no_advance",
        "tool_output_echo",
    }
)

# Consecutive bad exits that feed the stuck-card circuit breaker.
CIRCUIT_BREAKER_EXITS = frozenset(
    {
        "phase_cycle_cap",
        "max_iterations",
        "tool_failure_stop",
        "plan_exhausted",
        "explore_budget_exhausted",
        "patch_budget_exhausted",
        "duplicate_tool",
        "ollama_fallback",
        "read_only_no_edits",
        "completed_text_only",
        "completed_with_writes_no_advance",
        "po_clarification_incomplete",
        "po_generation_truncated",
    }
)

# Auto-sprint early-interrupt storm detection.
_interrupt_streak: int = 0
_interrupt_backoff_until: float = 0.0


def reset_interrupt_backoff_state() -> None:
    global _interrupt_streak, _interrupt_backoff_until
    _interrupt_streak = 0
    _interrupt_backoff_until = 0.0


def unhealthy_exit_blocks_lane_advance(
    exit_reason: Optional[str],
    ws: Optional[Dict[str, Any]] = None,
) -> bool:
    """True when Dev must stay In Progress despite prior writes."""
    if ws is None:
        from backend.services.workflow_settings import get_workflow_settings

        ws = get_workflow_settings()
    if ws.get("forceCompleteOnUnhealthyExit"):
        return False
    reason = str(exit_reason or "").strip().lower()
    if not reason:
        return False
    return reason in UNHEALTHY_LANE_ADVANCE_EXITS


def provisional_dev_exit_reason(
    *,
    agent_result: Optional[str],
    tools_used: Optional[Set[str]] = None,
    lane_before: str = "In Progress",
    lane_after: str = "In Progress",
) -> str:
    """Derive exit reason before lane moves (same rules as finalize)."""
    from backend.services.step_diagnostics import derive_exit_reason

    return derive_exit_reason(
        agent_result=agent_result,
        tools_used=tools_used,
        lane_before=lane_before,
        lane_after=lane_after,
    )


def patch_fingerprint(path: str, old_text: str = "", summary: str = "") -> str:
    """Stable fingerprint for identical failed apply_patch attempts."""
    raw = f"{str(path or '').replace(chr(92), '/')}|{old_text}|{summary}"
    return hashlib.sha256(raw.encode("utf-8", errors="replace")).hexdigest()[:16]


def record_failed_patch_fingerprint(task: Dict[str, Any], fingerprint: str) -> int:
    """Track identical patch failures across steps on a card. Returns consecutive count."""
    if not isinstance(task, dict) or not fingerprint:
        return 0
    prev = str(task.get("lastFailedPatchFingerprint") or "")
    if prev == fingerprint:
        count = int(task.get("identicalPatchFailCount") or 0) + 1
    else:
        count = 1
        task["lastFailedPatchFingerprint"] = fingerprint
    task["identicalPatchFailCount"] = count
    return count


def clear_patch_fingerprint(task: Dict[str, Any]) -> None:
    if not isinstance(task, dict):
        return
    task.pop("lastFailedPatchFingerprint", None)
    task.pop("identicalPatchFailCount", None)


def record_consecutive_bad_exit(
    task: Dict[str, Any],
    exit_reason: Optional[str],
    *,
    progress_made: bool = False,
) -> int:
    """Increment consecutive unhealthy exits; reset on healthy completion."""
    if not isinstance(task, dict):
        return 0
    reason = str(exit_reason or "").strip().lower()
    if reason == "completed_with_writes" and not progress_made:
        reason = "completed_with_writes_no_advance"
    if reason in CIRCUIT_BREAKER_EXITS:
        prev = str(task.get("lastCircuitExitReason") or "")
        if prev == reason or prev in CIRCUIT_BREAKER_EXITS:
            count = int(task.get("consecutiveBadExits") or 0) + 1
        else:
            count = 1
        task["consecutiveBadExits"] = count
        task["lastCircuitExitReason"] = reason
        return count
    # Healthy / lane-progress exits clear the streak.
    if (reason == "completed_with_writes" and progress_made) or reason == "po_clarified" or not reason:
        task["consecutiveBadExits"] = 0
        task.pop("lastCircuitExitReason", None)
        clear_patch_fingerprint(task)
        return 0
    return int(task.get("consecutiveBadExits") or 0)


def circuit_breaker_should_trip(task: Dict[str, Any], ws: Optional[Dict[str, Any]] = None) -> tuple[bool, str]:
    """
    Return (trip, reason) when the card should stop endless In Progress retries.
    Trips on consecutive bad exits OR identical apply_patch failures across steps.
    """
    if ws is None:
        from backend.services.workflow_settings import get_workflow_settings

        ws = get_workflow_settings()
    if not ws.get("enableStuckCircuitBreaker", True):
        return False, ""
    max_bad = max(1, int(ws.get("circuitBreakerMaxBadExits") or 3))
    max_patch = max(1, int(ws.get("circuitBreakerIdenticalPatchFails") or 3))
    bad = int(task.get("consecutiveBadExits") or 0)
    patch_fails = int(task.get("identicalPatchFailCount") or 0)
    if bad >= max_bad:
        return True, (
            f"Circuit breaker: {bad} consecutive unhealthy exits "
            f"({task.get('lastCircuitExitReason') or 'unknown'})"
        )
    if patch_fails >= max_patch:
        return True, (
            f"Circuit breaker: identical apply_patch failed {patch_fails} times — "
            "split the card or change approach"
        )
    return False, ""


def latch_needs_po_auto_skip(task: Dict[str, Any], *, reason: str = "") -> None:
    """Stop auto-sprint from re-picking this Needs PO card after empty/incomplete loops."""
    if not isinstance(task, dict):
        return
    task["poAutoSkip"] = True
    if reason:
        task["poAutoSkipReason"] = str(reason)[:300]


def needs_po_should_skip_auto(
    task: Dict[str, Any], ws: Optional[Dict[str, Any]] = None
) -> bool:
    if not isinstance(task, dict):
        return False
    if task.get("poAutoSkip"):
        return True
    trip, _ = circuit_breaker_should_trip(task, ws)
    return trip


def stuck_is_explore_without_write(task: Dict[str, Any]) -> bool:
    """True when the last Dev step burned explore tools and never wrote."""
    if not isinstance(task, dict):
        return False
    outcome = task.get("lastStepOutcome") or {}
    reason = str(
        outcome.get("exitReason") or outcome.get("stopReason") or ""
    ).strip().lower()
    if reason == "explore_budget_exhausted":
        return True
    return str(task.get("lastCircuitExitReason") or "").strip().lower() == (
        "explore_budget_exhausted"
    )


def should_force_patch_next_dev_step(task: Dict[str, Any]) -> bool:
    if not isinstance(task, dict):
        return False
    if task.get("forcePatchNextDevStep"):
        return True
    return stuck_is_explore_without_write(task)


def note_early_interrupt(
    *,
    exit_reason: Optional[str],
    ollama_call_count: int,
    duration_ms: int,
    tool_call_count: int = 0,
    ws: Optional[Dict[str, Any]] = None,
) -> float:
    """
    Track fast zero-work failures. Returns seconds to sleep before the next
    auto-sprint tick (0 = no backoff).
    """
    global _interrupt_streak, _interrupt_backoff_until
    if ws is None:
        from backend.services.workflow_settings import get_workflow_settings

        ws = get_workflow_settings()
    if not ws.get("enableAutoSprintInterruptBackoff", True):
        return 0.0

    reason = str(exit_reason or "").strip().lower()
    early = (
        reason in (CIRCUIT_BREAKER_EXITS | {"interrupted", "step_timeout"})
        and int(ollama_call_count or 0) == 0
        and int(tool_call_count or 0) == 0
        and int(duration_ms or 0) < int(ws.get("interruptEarlyMaxMs") or 30000)
    )
    if not early:
        _interrupt_streak = 0
        _interrupt_backoff_until = 0.0
        return 0.0

    _interrupt_streak += 1
    base = float(ws.get("autoSprintInterruptBackoffSec") or 5)
    max_backoff = float(ws.get("autoSprintInterruptBackoffMaxSec") or 120)
    # Exponential: base * 2^(streak-1), capped.
    delay = min(max_backoff, base * (2 ** max(0, _interrupt_streak - 1)))
    _interrupt_backoff_until = time.monotonic() + delay
    return delay


def remaining_interrupt_backoff_sec() -> float:
    remaining = _interrupt_backoff_until - time.monotonic()
    return max(0.0, remaining)


def note_zero_work_exit(
    watchdog: Dict[str, Any],
    *,
    task_id: str,
    exit_reason: str,
    ollama_call_count: int,
    tool_call_count: int,
    ws: Optional[Dict[str, Any]] = None,
) -> bool:
    """Return True when a third consecutive identical zero-work exit must pause."""
    if ws is None:
        from backend.services.workflow_settings import get_workflow_settings

        ws = get_workflow_settings()
    if not ws.get("enableZeroWorkRetryWatchdog", True):
        watchdog.clear()
        return False
    key = (str(task_id or ""), str(exit_reason or "").strip().lower())
    zero_work = bool(
        key[0]
        and key[1]
        and int(ollama_call_count or 0) == 0
        and int(tool_call_count or 0) == 0
    )
    if not zero_work:
        watchdog.clear()
        return False
    if watchdog.get("key") == key:
        watchdog["streak"] = int(watchdog.get("streak") or 0) + 1
    else:
        watchdog["key"] = key
        watchdog["streak"] = 1
    maximum = max(1, int(ws.get("zeroWorkRetryWatchdogMax") or 3))
    return int(watchdog["streak"]) >= maximum


def phase_cycle_cap_reached(cycle: int, ws: Optional[Dict[str, Any]] = None) -> bool:
    if ws is None:
        from backend.services.workflow_settings import get_workflow_settings

        ws = get_workflow_settings()
    cap = max(1, int(ws.get("maxDevPhaseCyclesPerCard") or 12))
    return int(cycle or 0) > cap


def _stored_phase_cycle(task: Dict[str, Any]) -> int:
    """Read the durable graph cycle without consulting global progress state."""
    progress = task.get("lastStepProgress")
    if not isinstance(progress, dict):
        return 0
    graph = progress.get("devPhaseGraph") or progress.get("dev_phase_graph")
    if not isinstance(graph, dict):
        return 0
    try:
        return max(0, int(graph.get("cycle") or 0))
    except (TypeError, ValueError):
        return 0


def begin_dev_step(
    task: Dict[str, Any],
    ws: Optional[Dict[str, Any]] = None,
) -> tuple[int, bool]:
    """
    Increment the card-level Dev visit exactly once per sprint Dev handler.
    Returns (visit_count, capped). A latched card never increments again.
    """
    if ws is None:
        from backend.services.workflow_settings import get_workflow_settings

        ws = get_workflow_settings()
    if task.get("phaseCycleCapReached"):
        return int(task.get("devStepCount") or 0), True

    prior = max(
        int(task.get("devStepCount") or 0),
        int(task.get("devPhaseCycle") or 0),
        _stored_phase_cycle(task),
    )
    visit = prior + 1
    task["devStepCount"] = visit
    task["devPhaseCycle"] = visit
    cap = max(1, int(ws.get("maxDevStepsPerCard") or ws.get("maxDevPhaseCyclesPerCard") or 12))
    if visit > cap:
        from datetime import datetime

        task["phaseCycleCapReached"] = True
        task["phaseCycleCapAt"] = visit
        task["phaseCycleCapReason"] = f"Developer visit budget exceeded ({visit}>{cap})"
        task["phaseCycleCapTimestamp"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        return visit, True
    return visit, False


def reset_dev_cycle_latch(task: Dict[str, Any]) -> None:
    """Explicit recovery reset; never called by ordinary PO/lane bounces."""
    task["devStepCount"] = 0
    task["devPhaseCycle"] = 0
    task["phaseCycleCapReached"] = False
    task["phaseCycleCapAt"] = None
    task["phaseCycleCapReason"] = None
    task["phaseCycleCapTimestamp"] = None
    task["latchedRecoveryAttempted"] = False
