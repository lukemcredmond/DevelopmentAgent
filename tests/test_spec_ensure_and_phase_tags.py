"""Spec ensure-on-work and phase tool tags."""

from pathlib import Path

from backend import state
from backend.agents.task_context import init_new_task, normalize_task
from backend.services.dev_phase_graph import phase_tag_for_tool
from backend.services.task_spec_markdown import (
    ensure_task_spec_for_work,
    task_spec_markdown_path,
)


def _put_on_backlog(task: dict) -> None:
    for lane, tasks in list(state.SHARED_BOARD.items()):
        if isinstance(tasks, list):
            state.SHARED_BOARD[lane] = [t for t in tasks if str(t.get("id")) != task["id"]]
    state.SHARED_BOARD.setdefault("Backlog", []).append(task)


def test_phase_tag_for_tool():
    assert phase_tag_for_tool("read_file") == "explore"
    assert phase_tag_for_tool("grep") == "explore"
    assert phase_tag_for_tool("apply_patch") == "patch"
    assert phase_tag_for_tool("write_file") == "patch"
    assert phase_tag_for_tool("run_test") == "verify"
    assert phase_tag_for_tool("run_command") == "verify"
    assert phase_tag_for_tool("unknown_tool") is None
    assert phase_tag_for_tool("") is None


def test_ensure_task_spec_builds_when_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "WORKSPACE_DIR", str(tmp_path))
    state.VIRTUAL_FILESYSTEM.clear()
    task = init_new_task(
        {
            "id": "T-SPEC-ENSURE-1",
            "title": "Ensure spec",
            "description": "Has a description for readiness",
            "acceptanceCriteria": ["A", "B"],
            "userStory": "As a user I want X so that Y",
        }
    )
    normalize_task(task)
    _put_on_backlog(task)

    path = ensure_task_spec_for_work(task["id"])
    assert path
    assert task.get("specMarkdownPath")
    content = state.VIRTUAL_FILESYSTEM.get(path) or ""
    assert "Ensure spec" in content
    phys = Path(tmp_path).joinpath(*task_spec_markdown_path(task["id"]).split("/"))
    assert phys.is_file()


def test_ensure_task_spec_reuses_existing_file(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "WORKSPACE_DIR", str(tmp_path))
    state.VIRTUAL_FILESYSTEM.clear()
    task = init_new_task(
        {
            "id": "T-SPEC-ENSURE-2",
            "title": "Existing file",
            "description": "Desc",
            "acceptanceCriteria": ["A", "B"],
        }
    )
    normalize_task(task)
    _put_on_backlog(task)

    rel = task_spec_markdown_path(task["id"])
    custom = "# Prebuilt Spec\n\nFrom file.\n"
    state.VIRTUAL_FILESYSTEM[rel] = custom
    phys = Path(tmp_path).joinpath(*rel.split("/"))
    phys.parent.mkdir(parents=True, exist_ok=True)
    phys.write_text(custom, encoding="utf-8")

    path = ensure_task_spec_for_work(task["id"])
    assert path
    assert "Prebuilt Spec" in (state.VIRTUAL_FILESYSTEM.get(path) or "")
    assert "Prebuilt Spec" in phys.read_text(encoding="utf-8")
