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

    stall_payload = {
        "status": "complete",
        "exitReason": "text_rejection_loop",
        "ok": False,
        "durationMs": 120000,
        "agent": "Developer",
        "taskId": "T-STALL",
        "taskTitle": "Scaffold polish",
        "resolvedEditTarget": "lib/main.dart",
        "backupSwitchOutcome": "blocked:vram",
        "performanceSummary": {"primaryBottleneck": "text_rejection_loop"},
        "ollamaCalls": [{"toolCalls": []}],
    }
    (diag / "step-stall.json").write_text(json.dumps(stall_payload), encoding="utf-8")
    rollup2 = build_sprint_diagnostics_rollup(project_id=pid, limit=10, since_hours=72)
    recent = rollup2.get("recentSteps") or []
    stall = next((s for s in recent if s.get("taskId") == "T-STALL"), None)
    assert stall is not None
    assert stall.get("resolvedEditTarget") == "lib/main.dart"
    assert stall.get("backupSwitchOutcome") == "blocked:vram"
    assert stall.get("primaryBottleneck") == "text_rejection_loop"
    assert stall.get("nativeToolCallRate") == 0.0

    precheck_payload = {
        "status": "complete",
        "exitReason": "dev_precheck_skip",
        "ok": False,
        "durationMs": 50,
        "agent": "Developer",
        "ollamaCallCount": 0,
        "promptTokensTotal": 0,
        "events": [{"kind": "dev_precheck_skip", "message": "latched"}],
        "taskId": "T0",
        "taskTitle": "Skip",
    }
    (diag / "step-precheck.json").write_text(json.dumps(precheck_payload), encoding="utf-8")
    rollup2 = build_sprint_diagnostics_rollup(project_id=pid, limit=10, since_hours=72)
    assert rollup2["devPrecheckSkipSteps"] >= 1
    assert rollup2["zeroTokenDevSteps"] >= 1

    patch_loop_payload = {
        "status": "complete",
        "exitReason": "tool_failure_stop",
        "toolFailures": 2,
        "agent": "Developer",
        "taskId": "T-PATCH",
        "taskTitle": "Patch loop",
        "performanceSummary": {"primaryBottleneck": "none"},
        "cardCumulativeState": {"identicalPatchFailCount": 3},
    }
    (diag / "step-patch-loop.json").write_text(json.dumps(patch_loop_payload), encoding="utf-8")
    rollup3 = build_sprint_diagnostics_rollup(project_id=pid, limit=20, since_hours=72)
    recent3 = rollup3.get("recentSteps") or []
    patch_row = next((s for s in recent3 if s.get("taskId") == "T-PATCH"), None)
    assert patch_row is not None
    assert patch_row.get("primaryBottleneck") == "patch_loop"
