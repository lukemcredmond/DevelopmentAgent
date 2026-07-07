"""System capacity and model recommendation tests."""

from backend.services.system_capacity import get_model_recommendations, probe_system_capacity


def test_probe_system_capacity_shape():
    data = probe_system_capacity()
    assert "tier" in data
    assert "gpuAvailable" in data


def test_model_recommendations_for_minimal_vram():
    capacity = {"tier": "minimal", "vramMb": 4096, "gpuAvailable": True}
    rec = get_model_recommendations(capacity, installed_models=["llama3:8b"])
    assert rec["roles"]["dev"]["model"].startswith("qwen2.5-coder")
    assert rec["roles"]["po"]["status"] == "installed"
