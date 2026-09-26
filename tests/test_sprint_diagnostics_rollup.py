"""Sprint diagnostics rollup from step JSON files."""

import json
from pathlib import Path

from backend import state
from backend.services.sprint_diagnostics_rollup import build_sprint_diagnostics_rollup


def test_rollup_counts_exit_reason_and_deadlock(tmp_path, monkeypatch):
    pid = "proj-rollup-test"
    state.CURRENT_PROJECT_ID = pid
    diag = tmp_path / pid
    diag.mkdir(parents=True)

    def fake_diagnostics_dir(project_id=None):
        return diag

    monkeypatch.setattr(
        "backend.services.sprint_diagnostics_rollup.diagnostics_dir",
        fake_diagnostics_dir,
    )

    payloads = [
        {
            "status": "complete",
            "exitReason": "meta_refusal",
            "ok": False,
            "durationMs": 1000,
            "textRefusalClass": "meta_refusal",
            "toolPolicyDeadlock": True,
            "toolPolicyDeadlockReason": "write_file blocked",
            "taskId": "T1",
            "taskTitle": "Export",
        },
        {
            "status": "complete",
            "exitReason": "completed_with_writes",
            "ok": True,
            "durationMs": 2000,
            "cursorLikenessScoreV2": True,
            "taskId": "T2",
            "taskTitle": "OK",
        },
        {
            "status": "running",
            "exitReason": "unknown",
            "taskId": "T3",
        },
    ]
    for i, p in enumerate(payloads):
        path = diag / f"step-T{i}.json"
        path.write_text(json.dumps(p), encoding="utf-8")

    rollup = build_sprint_diagnostics_rollup(
        project_id=pid,
        limit=10,
        since_hours=72,
        duplicate_summary={"duplicateClusterCount": 2, "duplicateExtraCount": 3},
    )
    assert rollup["stepsTotal"] == 2
    assert rollup["exitReason"]["meta_refusal"] == 1
    assert rollup["textRefusalClass"]["meta_refusal"] == 1
    assert rollup["toolPolicyDeadlock"]["true"] == 1
    assert rollup["duplicateClusterCount"] == 2
    assert rollup["okRate"] == 0.5
