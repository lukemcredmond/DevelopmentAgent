"""Global + project custom tools merge and Tool Lab UI markers."""

from __future__ import annotations

from pathlib import Path

from backend import state
from backend.bootstrap import initialize
from backend.services.custom_tools import (
    build_custom_tools,
    list_custom_tool_defs,
    list_project_custom_tool_defs,
    load_global_custom_tools,
    merged_custom_tool_defs,
    save_global_custom_tools,
)
from backend.services.workflow_settings import get_workflow_settings, save_workflow_settings


def _shell_tool(name: str, **extra):
    return {
        "name": name,
        "description": f"Tool {name}",
        "parameters": {"type": "object", "properties": {}, "required": []},
        "agents": ["Developer"],
        "executor": "shell",
        "shell": {"command": f"echo {name}"},
        **extra,
    }


def test_merged_project_overrides_global_same_name():
    initialize()
    save_global_custom_tools([_shell_tool("shared_tool", description="from global")])
    ws = get_workflow_settings()
    ws["customTools"] = [_shell_tool("shared_tool", description="from project")]
    save_workflow_settings(ws)

    merged = merged_custom_tool_defs(ws)
    by_name = {d["name"]: d for d in merged}
    assert "shared_tool" in by_name
    assert by_name["shared_tool"]["scope"] == "project"
    assert by_name["shared_tool"]["description"] == "from project"


def test_merged_keeps_distinct_global_and_project():
    initialize()
    save_global_custom_tools([_shell_tool("global_only")])
    ws = get_workflow_settings()
    ws["customTools"] = [_shell_tool("project_only")]
    save_workflow_settings(ws)

    names = {d["name"] for d in merged_custom_tool_defs(ws)}
    assert "global_only" in names
    assert "project_only" in names
    scopes = {d["name"]: d["scope"] for d in merged_custom_tool_defs(ws)}
    assert scopes["global_only"] == "global"
    assert scopes["project_only"] == "project"


def test_save_global_custom_tools_round_trip():
    initialize()
    saved = save_global_custom_tools([_shell_tool("round_trip_tool")])
    assert len(saved) == 1
    assert saved[0]["name"] == "round_trip_tool"
    assert saved[0]["scope"] == "global"

    loaded = load_global_custom_tools()
    assert any(d["name"] == "round_trip_tool" for d in loaded)
    # storage key present
    raw = state.storage.get_setting("global_custom_tools")
    assert raw and "round_trip_tool" in raw


def test_build_custom_tools_registers_merged():
    initialize()
    save_global_custom_tools([_shell_tool("g_echo")])
    ws = get_workflow_settings()
    ws["customTools"] = [_shell_tool("p_echo")]
    save_workflow_settings(ws)
    tools = build_custom_tools(ws)
    names = {t.name for t in tools}
    assert "g_echo" in names
    assert "p_echo" in names


def test_list_custom_includes_scope():
    initialize()
    save_global_custom_tools([_shell_tool("scoped_g")])
    ws = get_workflow_settings()
    ws["customTools"] = []
    save_workflow_settings(ws)
    defs = list_custom_tool_defs(ws)
    assert any(d.get("scope") == "global" and d["name"] == "scoped_g" for d in defs)
    assert list_project_custom_tool_defs(ws) == []


def test_catalog_api_includes_scope():
    from fastapi.testclient import TestClient

    from backend.main import app

    initialize()
    save_global_custom_tools([_shell_tool("catalog_global")])
    client = TestClient(app)
    res = client.get("/api/tools/catalog")
    assert res.status_code == 200
    data = res.json()
    customs = data.get("customTools") or []
    hit = next((c for c in customs if c.get("name") == "catalog_global"), None)
    assert hit is not None
    assert hit.get("scope") == "global"
    assert hit.get("kind") == "custom"


def test_put_get_custom_api():
    from fastapi.testclient import TestClient

    from backend.main import app

    initialize()
    client = TestClient(app)
    payload = {"scope": "global", "tools": [_shell_tool("api_global_tool")]}
    put = client.put("/api/tools/custom", json=payload)
    assert put.status_code == 200, put.text
    body = put.json()
    assert body["ok"] is True
    assert any(t["name"] == "api_global_tool" for t in body["tools"])

    get = client.get("/api/tools/custom?scope=global")
    assert get.status_code == 200
    assert any(t["name"] == "api_global_tool" for t in get.json()["tools"])


def test_ui_markers_tool_lab():
    root = Path(__file__).resolve().parents[1]
    tools_panel = (root / "frontend" / "src" / "components" / "ToolsPanel.tsx").read_text(
        encoding="utf-8"
    )
    assert "Custom tools" in tools_panel
    assert "CustomToolsEditor" in tools_panel
    editor = (root / "frontend" / "src" / "components" / "CustomToolsEditor.tsx").read_text(
        encoding="utf-8"
    )
    assert "Global (all projects)" in editor
    assert "This project" in editor
    agent_panel = (root / "frontend" / "src" / "components" / "AgentToolsPanel.tsx").read_text(
        encoding="utf-8"
    )
    assert "Open Tools → Custom tools" in agent_panel
