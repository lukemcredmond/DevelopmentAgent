"""Support bundle zip export."""

import io
import json
import zipfile

from backend import state
from backend.bootstrap import initialize
from backend.services.support_bundle import build_support_bundle_bytes


def test_build_support_bundle_includes_core_files(tmp_path, monkeypatch):
    monkeypatch.setenv("ALLHANDS_HOME", str(tmp_path))
    initialize()
    state.CURRENT_PROJECT_ID = "bundle-proj"
    state.PROJECT_NAME = "Bundle Test"
    state.SYSTEM_LOGS = [{"role": "System", "level": "info", "message": "hello"}]
    state.SHARED_BOARD = {"In Progress": [], "Needs User": [], "Done": []}

    raw = build_support_bundle_bytes(max_diagnostics=5)
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        names = set(zf.namelist())
        assert "console_logs.json" in names
        assert "board_snapshot.json" in names
        assert "workflow_settings.json" in names
        assert "environment.txt" in names
        logs = json.loads(zf.read("console_logs.json"))
        assert logs[0]["message"] == "hello"
