"""In-step read_file cache skips redundant reads when mtime unchanged."""

import os
from unittest.mock import MagicMock

from backend import state
from backend.bootstrap import initialize
from backend.services.step_read_cache import StepReadCache, get_step_read_cache


def test_read_cache_hit_when_mtime_unchanged(tmp_path, monkeypatch):
    monkeypatch.setenv("ALLHANDS_HOME", str(tmp_path))
    initialize()
    ws = tmp_path / "ws"
    ws.mkdir()
    target = ws / "lib" / "main.dart"
    target.parent.mkdir(parents=True)
    target.write_text("void main() {}\n", encoding="utf-8")
    state.WORKSPACE_DIR = str(ws)

    cache = StepReadCache()
    output = "--- lib/main.dart ---\nvoid main() {}\n"
    cache.put("lib/main.dart", output)
    assert cache.get("lib/main.dart") == output

    target.write_text("void main() { print('x'); }\n", encoding="utf-8")
    assert cache.get("lib/main.dart") is None


def test_get_step_read_cache_on_agent():
    agent = MagicMock()
    c1 = get_step_read_cache(agent)
    c2 = get_step_read_cache(agent)
    assert c1 is c2
