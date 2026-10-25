"""Sprint throughput metrics."""

from backend import state
from backend.services.sprint_report import apply_rollups, begin_sprint_report, finalize_sprint_report
from backend.services.sprint_throughput import sprint_report_throughput, workspace_loc_snapshot


def test_sprint_report_throughput_done_per_hour():
    report = {
        "startedAt": "2026-09-27 20:00:00",
        "endedAt": "2026-09-27 22:00:00",
        "stepsRun": 20,
        "laneCountsStart": {"Done": 2, "Needs PO": 10},
        "laneCountsEnd": {"Done": 4, "Needs PO": 12},
        "workspaceLocStart": {"dartLines": 100},
        "workspaceLocEnd": {"dartLines": 150},
    }
    tp = sprint_report_throughput(report)
    assert tp["doneDelta"] == 2
    assert tp["needsPoDelta"] == 2
    assert tp["donePerHour"] == 1.0
    assert tp["dartLocDelta"] == 50


def test_begin_sprint_report_includes_throughput(tmp_path):
    state.WORKSPACE_DIR = str(tmp_path)
    (tmp_path / "lib").mkdir()
    (tmp_path / "lib" / "main.dart").write_text("void main() {}\n", encoding="utf-8")
    report = begin_sprint_report(restart=True)
    rolled = apply_rollups(report)
    assert rolled.get("workspaceLocStart", {}).get("dartLines", 0) >= 1
    assert rolled.get("throughput") is not None
    finished = finalize_sprint_report(5, status="max_steps")
    assert finished.get("workspaceLocEnd") is not None


def test_workspace_loc_snapshot_counts_dart(tmp_path):
    state.WORKSPACE_DIR = str(tmp_path)
    (tmp_path / "a.dart").write_text("line1\nline2\n", encoding="utf-8")
    snap = workspace_loc_snapshot()
    assert snap["dartLines"] == 2
    assert snap["dartFiles"] == 1
