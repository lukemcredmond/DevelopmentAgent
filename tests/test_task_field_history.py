"""Tests for per-card field history (title/description/AC/SDD)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.services.task_field_history import (
    MAX_PER_FIELD,
    list_field_history,
    record_task_field_change,
    record_task_fields_from_update,
    serialize_field_value,
    sdd_snapshot_from_task,
)
from backend.storage.project_storage import ProjectStorage


@pytest.fixture
def storage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ProjectStorage:
    db = tmp_path / "test.db"
    store = ProjectStorage(db_path=str(db))
    from backend import state

    monkeypatch.setattr(state, "storage", store)
    monkeypatch.setattr(state, "CURRENT_PROJECT_ID", "proj-hist")
    return store


def test_serialize_sdd_and_ac_round_trip():
    ac = serialize_field_value("acceptanceCriteria", ["A", "B", ""])
    assert json.loads(ac) == ["A", "B"]
    sdd = serialize_field_value(
        "sdd",
        {"userStory": "as a user", "scope": "x", "outOfScope": "", "testPlan": "t"},
    )
    obj = json.loads(sdd)
    assert obj["userStory"] == "as a user"
    assert obj["scope"] == "x"


def test_record_skips_identical_and_keeps_baseline(storage: ProjectStorage):
    tid = "T-1"
    eid1 = record_task_field_change(
        tid,
        "description",
        "new text",
        old_value="old text",
        source="user",
        project_id="proj-hist",
    )
    assert eid1
    rows = storage.get_task_field_changelog("proj-hist", tid, "description", limit=10)
    assert len(rows) == 2
    assert rows[0]["value"] == "new text"
    assert rows[1]["value"] == "old text"
    assert rows[1]["source"] == "baseline"

    eid2 = record_task_field_change(
        tid,
        "description",
        "new text",
        old_value="new text",
        source="user",
        project_id="proj-hist",
    )
    assert eid2 is None
    assert len(storage.get_task_field_changelog("proj-hist", tid, "description", limit=10)) == 2


def test_cap_enforced(storage: ProjectStorage):
    tid = "T-CAP"
    for i in range(MAX_PER_FIELD + 5):
        record_task_field_change(
            tid,
            "title",
            f"title-{i}",
            old_value=f"title-{i - 1}" if i else None,
            source="user",
            project_id="proj-hist",
        )
    rows = storage.get_task_field_changelog("proj-hist", tid, "title", limit=100)
    assert len(rows) <= MAX_PER_FIELD
    assert rows[0]["value"] == f"title-{MAX_PER_FIELD + 4}"


def test_sdd_snapshot_from_update(storage: ProjectStorage):
    before = {
        "title": "T",
        "description": "d",
        "acceptanceCriteria": ["a"],
        "userStory": "old",
        "scope": "",
        "outOfScope": "",
        "testPlan": "",
    }
    task = {
        "id": "T-SDD",
        **before,
        "userStory": "new story",
        "scope": "in scope",
    }
    ids = record_task_fields_from_update(
        task,
        before=before,
        source="user",
        changed_keys=["userStory", "scope"],
    )
    assert ids
    snap = storage.get_task_field_changelog("proj-hist", "T-SDD", "sdd", limit=5)
    assert snap
    body = json.loads(snap[0]["value"])
    assert body["userStory"] == "new story"
    assert body["scope"] == "in scope"
    assert sdd_snapshot_from_task(task)["userStory"] == "new story"


def test_list_field_history_public_shape(storage: ProjectStorage):
    record_task_field_change(
        "T-L",
        "description",
        "hello world " * 20,
        old_value="prior",
        source="po",
        project_id="proj-hist",
    )
    entries = list_field_history("T-L", "description", project_id="proj-hist")
    assert entries
    assert "preview" in entries[0]
    assert "value" not in entries[0]
    assert entries[0]["source"] in ("po", "baseline")
