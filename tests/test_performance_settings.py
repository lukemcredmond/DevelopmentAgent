"""Performance settings normalization and effective model routing."""

from backend.services.agent_efficiency import effective_role_model
from backend.services.sampling import sampling_options_for_role
from backend.services.workflow_settings import (
    DEFAULT_WORKFLOW_SETTINGS,
    migrate_performance_settings,
    normalize_performance_settings,
)


def test_normalize_drops_empty_dev_models():
    out = normalize_performance_settings(
        {**DEFAULT_WORKFLOW_SETTINGS, "devExploreModel": "", "devPatchModel": "  "}
    )
    assert "devExploreModel" not in out
    assert "devPatchModel" not in out
    assert out.get("devExploreModel", "qwen2.5-coder:7b") == "qwen2.5-coder:7b"


def test_migrate_backfills_missing_performance_keys():
    saved = {"maxSprintSteps": 20, "devExploreModel": ""}
    out = migrate_performance_settings({**DEFAULT_WORKFLOW_SETTINGS, **saved})
    assert out.get("devExploreMaxTools") == 8
    assert out.get("ollamaNumCtxAdaptive") is True
    assert out.get("devExploreForcePatchInStep") is True
    assert "devExploreModel" not in out or out.get("devExploreModel") == "qwen2.5-coder:7b"


def test_po_sampling_clamped_without_override():
    ws = {
        "samplingByRole": {"po": {"num_predict": 2048}},
        "poNumPredictOverride": False,
    }
    opts = sampling_options_for_role("Product Owner", ws=ws)
    assert opts["num_predict"] == 1024


def test_po_sampling_honors_explicit_override():
    ws = {
        "samplingByRole": {"po": {"num_predict": 2048}},
        "poNumPredictOverride": True,
    }
    opts = sampling_options_for_role("Product Owner", ws=ws)
    assert opts["num_predict"] == 2048


def test_effective_role_model_routes_po_off_heavy_primary():
    model, reason = effective_role_model(
        role="Product Owner",
        primary_model="qwen/qwen3.8-27b:latest",
        ws={
            "singleModelMode": "off",
            "enablePhaseModelRouting": True,
            "devExploreModel": "qwen2.5-coder:7b",
            "discordModelPresetFast": "qwen2.5-coder:7b",
        },
    )
    assert model == "qwen2.5-coder:7b"
    assert "heavy_primary" in reason
