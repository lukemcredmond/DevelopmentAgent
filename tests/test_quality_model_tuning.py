"""Quality-first model tuning for 12GB GPUs."""

from __future__ import annotations

import json
from pathlib import Path

from backend.services.agent_efficiency import resolve_step_model, single_model_mode_active
from backend.services.sampling import sampling_options_for_role


def test_sidecar_quality_defaults():
    root = Path(__file__).resolve().parents[1]
    data = json.loads((root / "allhands.project.json").read_text(encoding="utf-8"))
    assert data["po_model"] == "qwen2.5-coder:14b"
    assert data["dev_model"] == "qwen2.5-coder:14b"
    ws = data["workflow_settings"]
    assert ws["enablePhaseModelRouting"] is False
    assert ws["singleModelMode"] == "on"
    assert ws["llmHostVramMb"] == 12288
    assert ws["devExploreForcePatchInStep"] is False
    assert ws["ollamaNumCtxAdaptiveStart"] == 8192


def test_single_model_14b_no_phase_explore_routing():
    ws = {
        "singleModelMode": "on",
        "enablePhaseModelRouting": False,
        "devExploreModel": "qwen2.5-coder:14b",
        "devPatchModel": "qwen2.5-coder:14b",
        "llmHostVramMb": 12288,
    }
    assert single_model_mode_active(ws) is True
    model, reason = resolve_step_model(
        role="Developer",
        phase="explore",
        primary_model="qwen2.5-coder:14b",
        ws=ws,
    )
    assert model == "qwen2.5-coder:14b"
    assert reason == "single_model"
    assert "phase_explore" not in reason


def test_po_sampling_still_capped_in_quality_mode():
    ws = {
        "samplingByRole": {"po": {"num_predict": 2048}},
        "poNumPredictOverride": False,
    }
    opts = sampling_options_for_role("Product Owner", ws=ws)
    assert opts["num_predict"] == 1024
