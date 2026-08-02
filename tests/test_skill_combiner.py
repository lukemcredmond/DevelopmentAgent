"""Project skill builder and skill path resolution."""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

from backend import state
from backend.agents.registry import agent_dev
from backend.bootstrap import initialize
from backend.services.skill_combiner import (
    BuiltSkillPathExistsError,
    build_combined_skill_markdown,
    combine_skills_preview,
    resolve_built_skill_slug,
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


def test_combine_uses_workspace_when_library_missing(tmp_path, monkeypatch):
    initialize()
    ws = tmp_path / "ws"
    lib = tmp_path / "lib"
    ws.mkdir()
    lib.mkdir()
    monkeypatch.setattr(state, "WORKSPACE_DIR", str(ws))
    monkeypatch.setattr(state, "SKILLS_DIR", str(lib))

    (lib / "a.md").write_text("# A\nlib\n", encoding="utf-8")
    ws_only = ws / "skills" / "built" / "only-ws.md"
    ws_only.parent.mkdir(parents=True)
    ws_only.write_text("# WS only\nworkspace rule\n", encoding="utf-8")

    captured: list = []

    def fake_merge(*, sources, **_kwargs):
        captured.extend(sources)
        return "# Merged\nok\n"

    with patch("backend.services.skill_combiner._merge_with_llm", side_effect=fake_merge):
        combine_skills_preview(
            agent_key="dev",
            skill_files=["a.md", "built/only-ws.md"],
        )

    by_rel = {s["rel"]: s["text"] for s in captured}
    assert "built/only-ws.md" in by_rel
    assert "workspace rule" in by_rel["built/only-ws.md"]


def test_combine_prefers_workspace_text_over_library(tmp_path, monkeypatch):
    initialize()
    ws = tmp_path / "ws"
    lib = tmp_path / "lib"
    ws.mkdir()
    lib.mkdir()
    monkeypatch.setattr(state, "WORKSPACE_DIR", str(ws))
    monkeypatch.setattr(state, "SKILLS_DIR", str(lib))

    rel = "edited.md"
    (lib / rel).write_text("# Lib\nlibrary text\n", encoding="utf-8")
    ws_file = ws / "skills" / rel
    ws_file.parent.mkdir(parents=True)
    ws_file.write_text("# WS\nworkspace edited text\n", encoding="utf-8")
    (lib / "other.md").write_text("# O\nother\n", encoding="utf-8")

    captured: list = []

    def fake_merge(*, sources, **_kwargs):
        captured.extend(sources)
        return "# M\n"

    with patch("backend.services.skill_combiner._merge_with_llm", side_effect=fake_merge):
        combine_skills_preview(agent_key="dev", skill_files=[rel, "other.md"])

    edited = next(s for s in captured if s["rel"] == rel)
    assert "workspace edited text" in edited["text"]
    assert "library text" not in edited["text"]


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
    assert result.get("mergeRounds") == 1


def test_default_built_skill_name_dev_combined(tmp_path, monkeypatch):
    initialize()
    ws = tmp_path / "ws"
    lib = tmp_path / "lib"
    ws.mkdir()
    lib.mkdir()
    monkeypatch.setattr(state, "WORKSPACE_DIR", str(ws))
    monkeypatch.setattr(state, "SKILLS_DIR", str(lib))
    (lib / "a.md").write_text("# a\n", encoding="utf-8")
    (lib / "b.md").write_text("# b\n", encoding="utf-8")

    with patch("backend.services.skill_combiner._merge_with_llm", return_value="# M\n"):
        result = combine_skills_preview(agent_key="dev", skill_files=["a.md", "b.md"])

    assert result["skillRel"] == "built/dev-combined.md"
    assert result["suggestedBasename"] == "dev-combined"
    assert result["fileExists"] is False


def test_default_built_skill_auto_suffix_when_exists(tmp_path, monkeypatch):
    initialize()
    ws = tmp_path / "ws"
    lib = tmp_path / "lib"
    ws.mkdir()
    lib.mkdir()
    monkeypatch.setattr(state, "WORKSPACE_DIR", str(ws))
    monkeypatch.setattr(state, "SKILLS_DIR", str(lib))
    (lib / "a.md").write_text("# a\n", encoding="utf-8")
    (lib / "b.md").write_text("# b\n", encoding="utf-8")
    existing = workspace_skill_path("built/dev-combined.md")
    os.makedirs(os.path.dirname(existing), exist_ok=True)
    with open(existing, "w", encoding="utf-8") as f:
        f.write("old\n")

    with patch("backend.services.skill_combiner._merge_with_llm", return_value="# M\n"):
        result = combine_skills_preview(agent_key="dev", skill_files=["a.md", "b.md"])

    assert result["skillRel"] == "built/dev-combined-2.md"
    assert result["fileExists"] is True
    assert result["requestedSkillRel"] == "built/dev-combined.md"


def test_save_built_skill_replace_existing(tmp_path, monkeypatch):
    initialize()
    ws = tmp_path / "ws"
    lib = tmp_path / "lib"
    ws.mkdir()
    lib.mkdir()
    monkeypatch.setattr(state, "WORKSPACE_DIR", str(ws))
    monkeypatch.setattr(state, "SKILLS_DIR", str(lib))

    dest = workspace_skill_path("built/dev-combined.md")
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, "w", encoding="utf-8") as f:
        f.write("old content\n")

    with pytest.raises(BuiltSkillPathExistsError):
        save_built_skill(
            skill_rel="built/dev-combined.md",
            markdown="# new\n",
            replace_existing=False,
        )

    save_built_skill(
        skill_rel="built/dev-combined.md",
        markdown="# new\n",
        replace_existing=True,
    )
    with open(dest, encoding="utf-8") as f:
        assert "new" in f.read()


def test_resolve_built_skill_slug_explicit_name(tmp_path, monkeypatch):
    initialize()
    ws = tmp_path / "ws"
    ws.mkdir()
    monkeypatch.setattr(state, "WORKSPACE_DIR", str(ws))
    monkeypatch.setattr(state, "SKILLS_DIR", str(ws / "lib"))

    slug, requested, exists, suggested = resolve_built_skill_slug("dev", "my-stack")
    assert slug == "my-stack.md"
    assert requested == "my-stack.md"
    assert exists is False
    assert suggested == "dev-combined"


def test_chained_merge_seven_sources(tmp_path, monkeypatch):
    initialize()
    ws = tmp_path / "ws"
    lib = tmp_path / "lib"
    ws.mkdir()
    lib.mkdir()
    monkeypatch.setattr(state, "WORKSPACE_DIR", str(ws))
    monkeypatch.setattr(state, "SKILLS_DIR", str(lib))

    names = [f"s{i}.md" for i in range(7)]
    for n in names:
        (lib / n).write_text(f"# {n}\n", encoding="utf-8")

    calls: list[list[str]] = []

    def fake_merge(*, sources, **_kwargs):
        calls.append([s["rel"] for s in sources])
        return "# merged\n"

    with patch("backend.services.skill_combiner._merge_with_llm", side_effect=fake_merge):
        result = combine_skills_preview(agent_key="dev", skill_files=names)

    assert len(calls) == 2
    assert len(calls[0]) == 5
    assert calls[1][0] == "_partial_merge_"
    assert len(calls[1]) == 3
    assert result["mergeRounds"] == 2
    assert result["sources"] == names
    for n in names:
        assert f"  - {n}" in result["markdown"]


def test_chained_merge_twelve_sources(tmp_path, monkeypatch):
    initialize()
    ws = tmp_path / "ws"
    lib = tmp_path / "lib"
    ws.mkdir()
    lib.mkdir()
    monkeypatch.setattr(state, "WORKSPACE_DIR", str(ws))
    monkeypatch.setattr(state, "SKILLS_DIR", str(lib))

    names = [f"t{i}.md" for i in range(12)]
    for n in names:
        (lib / n).write_text(f"# {n}\n", encoding="utf-8")

    calls: list[list[str]] = []

    def fake_merge(*, sources, **_kwargs):
        calls.append([s["rel"] for s in sources])
        return "# merged\n"

    with patch("backend.services.skill_combiner._merge_with_llm", side_effect=fake_merge):
        result = combine_skills_preview(agent_key="dev", skill_files=names)

    assert len(calls) == 3
    assert result["mergeRounds"] == 3
    assert result["sources"] == names
    assert calls[1][0] == "_partial_merge_"
    assert calls[2][0] == "_partial_merge_"


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
