"""Embed model workflow settings tests."""

from backend.bootstrap import initialize
from backend.storage.memory_engine import create_memory_engine, resolve_embed_model


def test_resolve_embed_model_from_workflow(monkeypatch):
    initialize()
    from backend.services.workflow_settings import save_workflow_settings

    save_workflow_settings({"embedModel": "nomic-embed-text:1.5"})
    assert resolve_embed_model() == "nomic-embed-text:1.5"


def test_create_memory_engine_uses_workflow_embed(monkeypatch):
    initialize()
    from backend.services.workflow_settings import save_workflow_settings

    save_workflow_settings({"embedModel": "nomic-embed-text:1.5"})
    engine = create_memory_engine()
    assert engine.embed_model == "nomic-embed-text:1.5"
