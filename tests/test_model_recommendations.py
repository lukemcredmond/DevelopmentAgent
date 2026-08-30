"""System capacity and model recommendation tests."""

from backend.services.system_capacity import get_model_recommendations, probe_system_capacity


def test_probe_system_capacity_shape():
    data = probe_system_capacity()
    assert "tier" in data
    assert "gpuAvailable" in data
    assert "vramUsedMb" in data
    assert "gpuUtilPct" in data


def test_model_recommendations_for_minimal_vram():
    capacity = {"tier": "minimal", "vramMb": 4096, "gpuAvailable": True}
    rec = get_model_recommendations(capacity, installed_models=["llama3:8b"])
    assert rec["roles"]["dev"]["model"].startswith("qwen2.5-coder")
    # A 4 GB card holds one small model; every role must share it rather than
    # recommending a second model that would force a reload on each lane change.
    assert rec["singleModelRecommended"] is True
    assert len({r["model"] for r in rec["roles"].values()}) == 1
    assert rec["roles"]["po"]["status"] == "not_installed"


def test_model_recommendations_never_suggest_14b_that_cannot_fit():
    """12 GB cannot hold a 14B plus a usable KV cache; it used to be recommended."""
    rec = get_model_recommendations({"tier": "low", "vramMb": 12288, "gpuAvailable": True})
    assert rec["roles"]["dev"]["model"] == "qwen2.5-coder:7b"


def test_model_recommendations_allow_14b_with_ample_vram():
    rec = get_model_recommendations({"tier": "high", "vramMb": 24576, "gpuAvailable": True})
    assert rec["roles"]["dev"]["model"] == "qwen2.5-coder:14b"
    assert rec["singleModelRecommended"] is False
