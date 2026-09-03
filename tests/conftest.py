"""Shared pytest hooks.

ALLHANDS_HOME is redirected before any backend import so ProjectStorage never
opens the live ~/.allhands/scrum_memory.db.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

_BOOTSTRAP_HOME = Path(tempfile.mkdtemp(prefix="allhands-pytest-boot-"))
os.environ["ALLHANDS_HOME"] = str(_BOOTSTRAP_HOME)

import backend.config as _config  # noqa: E402

_config.migrate_legacy_database = lambda: None  # type: ignore[method-assign]
_config.DB_PATH = str(_BOOTSTRAP_HOME / "scrum_memory.db")

from backend.storage import project_storage as _project_storage  # noqa: E402
from backend.storage import memory_engine as _memory_engine  # noqa: E402

_project_storage.DB_PATH = _config.DB_PATH
_memory_engine.DB_PATH = _config.DB_PATH

import pytest  # noqa: E402

from backend import state  # noqa: E402
from backend.services.workflow_settings import reset_workflow_settings  # noqa: E402
from backend.storage.project_storage import ProjectStorage  # noqa: E402


@pytest.fixture(autouse=True)
def _isolate_allhands_home(tmp_path, monkeypatch):
    """Per-test SQLite + ALLHANDS_HOME. Never touch the operator's live DB."""
    home = tmp_path / "allhands"
    home.mkdir()
    db_path = str(home / "scrum_memory.db")
    monkeypatch.setenv("ALLHANDS_HOME", str(home))
    monkeypatch.setattr(_config, "migrate_legacy_database", lambda: None)
    monkeypatch.setattr(_config, "DB_PATH", db_path)
    monkeypatch.setattr(_project_storage, "DB_PATH", db_path)
    monkeypatch.setattr(_memory_engine, "DB_PATH", db_path)
    state.storage = ProjectStorage(db_path)
    yield


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
