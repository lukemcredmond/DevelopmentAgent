"""Model benchmark file backups."""

import json
import os
from pathlib import Path

from backend.bootstrap import initialize
from backend.services.model_benchmark_backup import (
    list_benchmark_backups,
    load_backup_file,
    write_benchmark_backup,
)


def test_write_and_list_backups(tmp_path, monkeypatch):
    monkeypatch.setenv("ALLHANDS_HOME", str(tmp_path))
    initialize()
    snap = {"status": "done", "results": [{"model": "a", "status": "done"}]}
    url = "http://localhost:11434"
    path = write_benchmark_backup(snap, url, "test")
    assert path is not None and path.is_file()
    items = list_benchmark_backups(url)
    assert len(items) == 1
    loaded = load_backup_file(items[0]["filename"])
    assert loaded is not None
    assert loaded.get("status") == "done"


def test_backup_retention(tmp_path, monkeypatch):
    monkeypatch.setenv("ALLHANDS_HOME", str(tmp_path))
    initialize()
    url = "http://127.0.0.1:11434"
    for i in range(35):
        write_benchmark_backup({"i": i}, url, f"n{i}")
    items = list_benchmark_backups(url, limit=50)
    assert len(items) <= 30
