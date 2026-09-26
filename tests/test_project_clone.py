"""Clone project for model experiments."""

import json

from backend import state
from backend.bootstrap import initialize, load_project_into_state
from backend.services.project_service import clone_project_for_experiment
from backend.services.workflow_settings import get_workflow_settings, save_workflow_settings
from backend.storage.project_storage import count_board_tasks


def test_clone_project_copies_brief_skills_resets_board(tmp_path, monkeypatch):
    monkeypatch.setenv("ALLHANDS_HOME", str(tmp_path))
    initialize()

    source_id = "source-proj-001"
    source_ws = tmp_path / "source_ws"
    clone_ws = tmp_path / "clone_ws"
    source_ws.mkdir(parents=True, exist_ok=True)

    board = {
        "Backlog": [{"id": "t1", "title": "Card", "priority": 1}],
        "In Progress": [],
        "Needs PO": [],
        "Needs User": [],
        "Refinement": [],
        "Blocked": [],
        "Code Review": [],
        "QA": [],
        "Done": [],
        "Features": [],
        "Pending Approval": [],
    }
    state.storage.save_project(
        source_id,
        "Source",
        "Build the auth service",
        str(source_ws),
        board,
        {"README.md": "# hi"},
        ["po-skill.md"],
        ["dev-skill.md"],
        [],
        ["qa-skill.md"],
        "model-po",
        "model-dev-old",
        "model-cr",
        "model-qa",
        plan_outline="## Plan\n- epic",
        original_brief="Original brief text",
        force_board=True,
    )
    save_workflow_settings({"requireCodeReview": False, "splitCardWhenAcOver": 7}, source_id)

    assert load_project_into_state(source_id)
    new_id = clone_project_for_experiment(
        source_id,
        name="Clone test",
        workspace_dir=str(clone_ws),
        dev_model="model-dev-new",
    )

    assert new_id != source_id
    assert state.CURRENT_PROJECT_ID == new_id

    cloned = state.storage.load_project(new_id)
    assert cloned is not None
    assert cloned["brief"] == "Build the auth service"
    assert cloned.get("original_brief") == "Original brief text"
    assert cloned.get("plan_outline") == ""
    assert cloned["dev_model"] == "model-dev-new"
    assert cloned["po_model"] == "model-po"
    assert cloned["po_skills"] == ["po-skill.md"]
    assert cloned["dev_skills"] == ["dev-skill.md"]
    assert count_board_tasks(cloned["board_state"]) == 0

    src_raw = state.storage.get_setting(f"workflow:{source_id}")
    dest_raw = state.storage.get_setting(f"workflow:{new_id}")
    assert src_raw and dest_raw
    assert json.loads(dest_raw)["splitCardWhenAcOver"] == 7
    assert get_workflow_settings(new_id)["requireCodeReview"] is False


def test_clone_rejects_same_workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("ALLHANDS_HOME", str(tmp_path))
    initialize()
    source_id = "src-same-ws"
    ws = str(tmp_path / "shared")
    ws_path = tmp_path / "shared"
    ws_path.mkdir()
    state.storage.save_project(
        source_id,
        "Source",
        "brief",
        ws,
        {"Backlog": []},
        {},
        [],
        [],
        [],
        [],
        "a",
        "b",
        "c",
        "d",
        force_board=True,
    )
    import pytest

    with pytest.raises(ValueError, match="must differ"):
        clone_project_for_experiment(
            source_id,
            name="Clone",
            workspace_dir=ws,
        )
