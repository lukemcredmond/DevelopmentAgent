"""Step diagnostics: time to first successful write."""

from backend.services.step_diagnostics import StepDiagnosticsTracker, _time_to_first_write_ms


def test_time_to_first_write_ms_from_tools_log():
    trace = StepDiagnosticsTracker(
        trace_id="t1",
        project_id="p1",
        task_id="task1",
        task_title="t",
        agent="Developer",
        lane_before="In Progress",
        file_path="/tmp/x.json",
    )
    trace.started_at = "2026-09-27 14:00:00"
    trace.tools_log = [
        {
            "toolName": "read_file",
            "success": True,
            "timestamp": "2026-09-27 14:00:10",
        },
        {
            "toolName": "apply_patch",
            "success": True,
            "timestamp": "2026-09-27 14:00:45",
        },
    ]
    ms = _time_to_first_write_ms(trace)
    assert ms == 45_000
