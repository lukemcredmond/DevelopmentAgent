"""Workspace structure audit, scaffold, and advance gate."""

from __future__ import annotations

import json
import os
from pathlib import Path

from backend.bootstrap import initialize
from backend.agents.task_context import init_new_task
from backend.services.workflow_settings import reset_workflow_settings, save_workflow_settings
from backend.services.workspace_structure_audit import (
    audit_workspace_structure,
    format_structure_audit,
    structure_ok,
)
from backend.services.workspace_scaffold import maybe_auto_scaffold, scaffold_python_stubs


def _write(ws: str, rel: str, content: str = "") -> None:
    path = os.path.join(ws, rel.replace("/", os.sep))
    os.makedirs(os.path.dirname(path) or ws, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def test_react_audit_missing_app(tmp_path, monkeypatch):
    from backend import state

    initialize()
    ws = str(tmp_path)
    state.WORKSPACE_DIR = ws
    _write(
        ws,
        "package.json",
        json.dumps(
            {
                "name": "demo",
                "dependencies": {"react": "^18.0.0"},
                "devDependencies": {"vite": "^5.0.0"},
            }
        ),
    )
    _write(ws, "index.html", "<html></html>")
    _write(ws, "src/main.tsx", "import './App'\n")
    audit = audit_workspace_structure(ws)
    assert audit["stack"] in ("react_vite", "react_next")
    assert any("App" in m for m in audit["missing"])
    assert audit["critical"] is True
    assert structure_ok(ws) is False

    _write(ws, "src/App.tsx", "export default function App(){return null}\n")
    audit2 = audit_workspace_structure(ws)
    assert not any("App" in m for m in audit2.get("missing") or [])
    assert structure_ok(ws) is True


def test_python_dotnet_unity_stack_ids(tmp_path):
    from backend import state

    initialize()
    # Python
    py = str(tmp_path / "py")
    os.makedirs(py)
    state.WORKSPACE_DIR = py
    _write(py, "pyproject.toml", '[project]\nname="x"\n')
    _write(py, "src/__init__.py", "")
    assert audit_workspace_structure(py)["stack"] == "python"

    # .NET
    dn = str(tmp_path / "dn")
    os.makedirs(dn)
    state.WORKSPACE_DIR = dn
    _write(
        dn,
        "App.csproj",
        '<Project Sdk="Microsoft.NET.Sdk.Web">\n</Project>\n',
    )
    audit_dn = audit_workspace_structure(dn)
    assert audit_dn["stack"] == "dotnet"
    assert audit_dn["critical"] is True  # missing Program.cs for web SDK

    _write(dn, "Program.cs", "var b = WebApplication.CreateBuilder(args);\n")
    assert structure_ok(dn) is True

    # Unity
    un = str(tmp_path / "un")
    os.makedirs(os.path.join(un, "Assets"))
    os.makedirs(os.path.join(un, "ProjectSettings"))
    state.WORKSPACE_DIR = un
    _write(un, "ProjectSettings/ProjectVersion.txt", "m_EditorVersion: 2022.3\n")
    audit_un = audit_workspace_structure(un)
    assert audit_un["stack"] == "unity_quest"
    assert audit_un["critical"] is False
    assert structure_ok(un) is True


def test_unknown_stack_no_hard_gate(tmp_path):
    from backend import state

    initialize()
    ws = str(tmp_path / "empty")
    os.makedirs(ws)
    state.WORKSPACE_DIR = ws
    audit = audit_workspace_structure(ws)
    assert audit["stack"] == "unknown"
    assert structure_ok(ws) is True


def test_dev_gate_blocks_on_structure(tmp_path, monkeypatch):
    from backend import state
    from backend.services.sprint_service import dev_gate_blocks_advance

    initialize()
    reset_workflow_settings()
    save_workflow_settings({"requireWorkspaceStructure": True, "requireCleanLint": False})
    ws = str(tmp_path / "react_gap")
    os.makedirs(ws)
    state.WORKSPACE_DIR = ws
    _write(
        ws,
        "package.json",
        json.dumps({"dependencies": {"react": "18"}, "devDependencies": {"vite": "5"}}),
    )
    task = init_new_task({"id": "T-STR", "title": "t", "description": "d"})
    task["files"] = [{"path": "src/x.tsx", "action": "written"}]
    blocked, reason = dev_gate_blocks_advance(task)
    assert blocked is True
    assert "structure" in reason.lower() or "missing" in reason.lower()


def test_python_scaffold_stubs(tmp_path):
    from backend import state

    initialize()
    reset_workflow_settings()
    save_workflow_settings({"autoScaffoldOnStructureGap": True})
    ws = str(tmp_path / "pyscaff")
    os.makedirs(ws)
    state.WORKSPACE_DIR = ws
    # Marker that selects python but incomplete
    _write(ws, "requirements.txt", "flask\n")
    task = init_new_task({"id": "T-PY", "title": "t", "description": "d"})
    result = maybe_auto_scaffold(task)
    assert result.get("ok") is True or result.get("structure_ok_after")
    assert os.path.isfile(os.path.join(ws, "src", "__init__.py")) or os.path.isfile(
        os.path.join(ws, "pyproject.toml")
    )
    # Second call capped
    again = maybe_auto_scaffold(task)
    assert again.get("skipped") == "already_attempted"


def test_scaffold_skipped_when_structured(tmp_path):
    from backend import state

    initialize()
    reset_workflow_settings()
    save_workflow_settings({"autoScaffoldOnStructureGap": True})
    ws = str(tmp_path / "ok")
    os.makedirs(ws)
    state.WORKSPACE_DIR = ws
    _write(
        ws,
        "package.json",
        json.dumps({"dependencies": {"react": "18"}, "devDependencies": {"vite": "5"}}),
    )
    _write(ws, "index.html", "<html></html>")
    _write(ws, "src/main.tsx", "")
    _write(ws, "src/App.tsx", "")
    task = init_new_task({"id": "T-OK", "title": "t", "description": "d"})
    result = maybe_auto_scaffold(task)
    assert result.get("skipped") == "not_eligible"


def test_prompt_and_ui_markers():
    root = Path(__file__).resolve().parents[1]
    sprint = (root / "backend" / "services" / "sprint_service.py").read_text(encoding="utf-8")
    assert "list_dir" in sprint
    assert "Structure first" in sprint
    assert "WORKSPACE STRUCTURE AUDIT" in sprint or "format_structure_audit" in sprint
    registry = (root / "backend" / "agents" / "registry.py").read_text(encoding="utf-8")
    assert "list_dir" in registry
    panel = (root / "frontend" / "src" / "components" / "WorkflowPanel.tsx").read_text(
        encoding="utf-8"
    )
    assert "requireWorkspaceStructure" in panel
    assert "autoScaffoldOnStructureGap" in panel
    readme = (root / "README.md").read_text(encoding="utf-8")
    assert "requireWorkspaceStructure" in readme


def test_format_audit_contains_header(tmp_path):
    from backend import state

    initialize()
    ws = str(tmp_path / "fmt")
    os.makedirs(ws)
    state.WORKSPACE_DIR = ws
    text = format_structure_audit()
    assert "WORKSPACE STRUCTURE AUDIT" in text


def test_collect_sprint_context_includes_src(tmp_path):
    from backend import state
    from backend.workspace.files import _collect_sprint_context_paths

    initialize()
    ws = str(tmp_path / "srcseed")
    os.makedirs(ws)
    state.WORKSPACE_DIR = ws
    _write(ws, "src/App.tsx", "export default function App(){return null}\n")
    task = {"files": []}
    paths = _collect_sprint_context_paths(task)
    assert any(p.endswith("App.tsx") or p == "src/App.tsx" for p in paths)
