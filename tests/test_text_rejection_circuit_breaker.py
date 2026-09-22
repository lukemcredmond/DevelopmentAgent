"""Text-rejection circuit breaker and exit reason mapping."""

from backend.services.step_diagnostics import derive_exit_reason, start_step_trace


def test_derive_exit_reason_text_rejection_loop():
    reason = derive_exit_reason(
        agent_result="Stopped: text rejection loop — identical text repeated (2×).",
        tools_used=set(),
        lane_before="In Progress",
        lane_after="In Progress",
    )
    assert reason == "text_rejection_loop"


def test_tracker_records_identical_text_reject():
    trace = start_step_trace("T-TEXT", "Title", "Developer", "In Progress")
    trace.log_event("identical_text_reject", "hash=abc count=2")
    trace.log_event("forced_tool_mode", "after 3 text rejects")
    assert trace.identical_text_reject_count == 1
    assert trace.forced_tool_mode is True
