"""Shared pytest hooks."""

import pytest

from backend.services.workflow_settings import reset_workflow_settings


@pytest.fixture(autouse=True)
def _reset_workflow_settings_after_test():
    yield
    try:
        reset_workflow_settings()
    except Exception:
        pass


@pytest.fixture(autouse=True)
def _isolate_capacity_probes(monkeypatch):
    """Keep capacity probing hermetic and host-independent.

    Two hazards otherwise: `fetch_model_meta` waits on an Ollama server that is not
    running, and `singleModelMode: auto` would resolve differently depending on the
    GPU of whatever machine runs the tests. Default to "capacity unknown"; tests that
    care patch these explicitly.
    """
    from backend.services import llm_capacity, system_capacity

    llm_capacity.clear_capacity_caches()
    monkeypatch.setattr(llm_capacity, "fetch_model_meta", lambda *a, **k: None)
    monkeypatch.setattr(
        system_capacity,
        "probe_system_capacity",
        lambda: {"gpuAvailable": False, "vramMb": None, "ramGb": None, "tier": "cpu_only"},
    )
    yield
    llm_capacity.clear_capacity_caches()
