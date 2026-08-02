"""Project skill builder and skill path resolution."""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

from backend import state
from backend.agents.registry import agent_dev
from backend.bootstrap import initialize
from backend.services.skill_combiner import (
    build_combined_skill_markdown,
    combine_skills_preview,
    save_built_skill,
)
from backend.services.skills import (
    library_skill_path,
    resolve_skill_read_path,
    workspace_skill_path,
)


def test_resolve_skill_read_path_prefers_workspace(tmp_path, monkeypatch):
    initialize()
    ws = tmp_path / "ws"
    lib = tmp_path / "lib"
    ws.mkdir()
    lib.mkdir()
    monkeypatch.setattr(state, "WORKSPACE_DIR", str(ws))
    monkeypatch.setattr(state, "SKILLS_DIR", str(lib))

    rel = "git_expert.md"
    lib_file = lib / rel
    ws_file = ws / "skills" / rel
    ws_file.parent.mkdir(parents=True)
    lib_file.write_text("library version", encoding="utf-8")
    ws_file.write_text("workspace version", encoding="utf-8")

    assert resolve_skill_read_path(rel) == str(ws_file)
    with open(resolve_skill_read_path(rel), encoding="utf-8") as f:
        assert f.read() == "workspace version"


def test_resolve_skill_read_path_falls_back_to_library(tmp_path, monkeypatch):
    initialize()
    ws = tmp_path / "ws"
    lib = tmp_path / "lib"
    ws.mkdir()
    lib.mkdir()
    monkeypatch.setattr(state, "WORKSPACE_DIR", str(ws))
    monkeypatch.setattr(state, "SKILLS_DIR", str(lib))

    rel = "only_lib.md"
    (lib / rel).write_text("from lib", encoding="utf-8")
    assert resolve_skill_read_path(rel) == str(lib / rel)


def test_get_skills_context_loads_workspace_built_skill(tmp_path, monkeypatch):
    initialize()
    ws = tmp_path / "ws"
    lib = tmp_path / "lib"
    ws.mkdir()
    lib.mkdir()
    monkeypatch.setattr(state, "WORKSPACE_DIR", str(ws))
    monkeypatch.setattr(state, "SKILLS_DIR", str(lib))

    rel = "built/combined.md"
    path = workspace_skill_path(rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("# Combined\nDo the thing.\n")

    agent_dev.assigned_skills = [rel]
    ctx = agent_dev._get_skills_context()
    assert "Combined" in ctx
    assert rel in ctx
    agent_dev.assigned_skills = []


def test_combine_skills_preview_mock_llm(tmp_path, monkeypatch):
    initialize()
    ws = tmp_path / "ws"
    lib = tmp_path / "lib"
    ws.mkdir()
    lib.mkdir()
    monkeypatch.setattr(state, "WORKSPACE_DIR", str(ws))
    monkeypatch.setattr(state, "SKILLS_DIR", str(lib))

    (lib / "a.md").write_text("# A\nRule one.\n", encoding="utf-8")
    (lib / "b.md").write_text("# B\nRule two.\n", encoding="utf-8")

    def fake_merge(**_kwargs):
        return "# Merged Skill\n- Rule one.\n- Rule two.\n"

    with patch(
        "backend.services.skill_combiner._merge_with_llm",
        side_effect=fake_merge,
    ):
        result = combine_skills_preview(
            agent_key="dev",
            skill_files=["a.md", "b.md"],
            output_name="my-stack",
        )

    assert result["skillRel"] == "built/my-stack.md"
    assert "Merged Skill" in result["markdown"]
    assert "sources:" in result["markdown"]
    assert result["sources"] == ["a.md", "b.md"]


def test_save_built_skill_writes_workspace(tmp_path, monkeypatch):
    initialize()
    ws = tmp_path / "ws"
    lib = tmp_path / "lib"
    ws.mkdir()
    lib.mkdir()
    monkeypatch.setattr(state, "WORKSPACE_DIR", str(ws))
    monkeypatch.setattr(state, "SKILLS_DIR", str(lib))

    md = build_combined_skill_markdown(
        agent_key="dev",
        skill_files=["a.md", "b.md"],
        body="# Test\nBody\n",
    )
    out = save_built_skill(skill_rel="built/test-merge.md", markdown=md)
    assert out["skillRel"] == "built/test-merge.md"
    dest = workspace_skill_path("built/test-merge.md")
    assert os.path.isfile(dest)
    assert state.VIRTUAL_FILESYSTEM.get("skills/built/test-merge.md")


def test_combine_requires_at_least_two():
    initialize()
    with pytest.raises(ValueError, match="at least two"):
        combine_skills_preview(agent_key="dev", skill_files=["only.md"])
