"""Configurable agent tools and custom SQL/shell/http tools."""

import json
import sqlite3

from backend import state
from backend.agents.registry import agent_dev, agent_po, configure_agent_tools
from backend.bootstrap import initialize
from backend.services.custom_tools import (
    QUERY_SQL_PRESET,
    _format_shell_command,
    execute_sql_tool,
)


def test_default_configure_includes_write_for_dev():
    initialize()
    state.REFINEMENT_MODE = False
    configure_agent_tools({"enableSemanticSearch": False, "enableWebSearch": False, "agentTools": {}})
    names = agent_dev.registry.tool_names()
    assert "write_file" in names
    assert "read_file" in names


def test_allowlist_removes_write_file_from_dev():
    initialize()
    state.REFINEMENT_MODE = False
    state.ACTIVE_SPRINT_AGENT = "Developer"
    configure_agent_tools(
        {
            "enableSemanticSearch": False,
            "enableWebSearch": False,
            "agentTools": {
                "Developer": ["read_file", "grep", "update_board"],
            },
            "customTools": [],
        }
    )
    names = agent_dev.registry.tool_names()
    assert "write_file" not in names
    assert "read_file" in names
    result = agent_dev.registry.invoke("write_file", {"path": "a.py", "content": "x"})
    assert "Error:" in result
    assert "Map it in the Tool Resolution" not in result


def test_refinement_strips_writes_even_with_allowlist():
    initialize()
    state.REFINEMENT_MODE = True
    configure_agent_tools(
        {
            "enableSemanticSearch": False,
            "enableWebSearch": False,
            "agentTools": {
                "Developer": ["read_file", "write_file", "run_command"],
            },
            "agentToolsAllowWritesInRefinement": False,
            "customTools": [],
        }
    )
    names = agent_dev.registry.tool_names()
    assert "write_file" not in names
    assert "run_command" not in names
    assert "read_file" in names
    state.REFINEMENT_MODE = False
    configure_agent_tools()


def test_query_sql_custom_tool_registers_and_runs(tmp_path, monkeypatch):
    initialize()
    state.REFINEMENT_MODE = False
    monkeypatch.setattr(state, "WORKSPACE_DIR", str(tmp_path))
    db_path = tmp_path / "data" / "app.db"
    db_path.parent.mkdir(parents=True)
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE meals (id INTEGER PRIMARY KEY, name TEXT)")
        conn.execute("INSERT INTO meals (name) VALUES ('Pasta')")
        conn.commit()

    tool_def = dict(QUERY_SQL_PRESET)
    tool_def["sql"] = {
        "connections": {"local": "sqlite:///data/app.db"},
        "readOnly": True,
        "maxRows": 50,
    }
    configure_agent_tools(
        {
            "enableSemanticSearch": False,
            "enableWebSearch": False,
            "agentTools": {},
            "customTools": [tool_def],
        }
    )
    names = agent_dev.registry.tool_names()
    assert "query_sql" in names
    ollama_tools = agent_dev.registry.get_ollama_tools()
    assert any(t["function"]["name"] == "query_sql" for t in ollama_tools)

    out = agent_dev.registry.invoke(
        "query_sql", {"db_name": "local", "query": "SELECT name FROM meals"}
    )
    data = json.loads(out)
    assert data["rows"][0]["name"] == "Pasta"


def test_sql_rejects_non_select():
    tool_def = {
        "name": "query_sql",
        "executor": "sql",
        "sql": {"connections": {"local": "sqlite:///x.db"}, "readOnly": True},
    }
    out = execute_sql_tool(tool_def, db_name="local", query="DELETE FROM meals")
    assert "read-only" in out.lower() or "Only read-only" in out


def test_shell_custom_formats_args():
    cmd = _format_shell_command(
        "python run.py --db {db_name} --q {query}",
        {"db_name": "a b", "query": "x;y"},
    )
    assert "a b" in cmd or "'a b'" in cmd or '"a b"' in cmd
    assert "x;y" in cmd or "'x;y'" in cmd

def test_tools_catalog_endpoint():
    initialize()
    from fastapi.testclient import TestClient
    from backend.main import app

    client = TestClient(app)
    resp = client.get("/api/tools/catalog")
    assert resp.status_code == 200
    body = resp.json()
    assert "builtins" in body
    assert any(b["name"] == "write_file" for b in body["builtins"])
    assert "agents" in body
    assert "Developer" in body["agents"]
    assert body["presets"]["query_sql"]["name"] == "query_sql"


def test_po_does_not_get_query_sql_by_default_agents():
    initialize()
    state.REFINEMENT_MODE = False
    configure_agent_tools(
        {
            "enableSemanticSearch": False,
            "enableWebSearch": False,
            "customTools": [QUERY_SQL_PRESET],
        }
    )
    assert "query_sql" in agent_dev.registry.tool_names()
    assert "query_sql" not in agent_po.registry.tool_names()


def test_shell_postprocess_dart_analyze():
    from backend.services.custom_tools import execute_shell_tool

    fake_out = (
        "  error • Expected a method body • lib/main.dart:10:5\n"
        "  warning • Unused import • lib/util.dart:1:8\n"
    )
    tool_def = {
        "name": "lint",
        "executor": "shell",
        "shell": {"command": "echo test"},
        "shellPostProcess": {"builtin": "dart_analyze"},
    }

    class FakeResult:
        combined_output = fake_out

    from backend.services import command_result as cr

    original = cr.run_workspace_command
    try:
        cr.run_workspace_command = lambda cmd, timeout=None: FakeResult()
        out = execute_shell_tool(tool_def)
    finally:
        cr.run_workspace_command = original

    assert "## Problems" in out or "main.dart:10" in out
    assert "error" in out.lower()


def test_script_executor_reads_stdin_json(tmp_path, monkeypatch):
    initialize()
    monkeypatch.setattr(state, "WORKSPACE_DIR", str(tmp_path))
    script = tmp_path / "tools" / "echo_args.py"
    script.parent.mkdir(parents=True)
    script.write_text(
        "import json, sys\nprint(json.dumps(json.load(sys.stdin)))\n",
        encoding="utf-8",
    )
    tool_def = {
        "name": "echo_args",
        "executor": "script",
        "script": {"path": "tools/echo_args.py"},
    }
    from backend.services.custom_tools import execute_script_tool

    out = execute_script_tool(tool_def, hello="world")
    data = json.loads(out)
    assert data["hello"] == "world"


def test_register_custom_tool_persists_and_registers(tmp_path, monkeypatch):
    initialize()
    state.REFINEMENT_MODE = False
    monkeypatch.setattr(state, "WORKSPACE_DIR", str(tmp_path))
    from backend.services.tool_forge_service import register_custom_tool

    result = register_custom_tool(
        {
            "name": "my_echo_tool",
            "description": "Echo via shell",
            "parameters": {"type": "object", "properties": {"msg": {"type": "string"}}},
            "agents": ["Developer"],
            "executor": "shell",
            "shell": {"command": "echo {msg}"},
        },
        source="test",
        agent_role="Developer",
    )
    assert result["ok"] is True
    configure_agent_tools(
        {
            "enableSemanticSearch": False,
            "enableWebSearch": False,
            "customTools": result.get("tools") or [],
        }
    )
    assert "my_echo_tool" in agent_dev.registry.tool_names()
    out = agent_dev.registry.invoke("my_echo_tool", {"msg": "hi"})
    assert "hi" in out


def test_validate_rejects_path_traversal():
    from backend.services.tool_forge_service import validate_custom_tool_def

    ok, errors, _ = validate_custom_tool_def(
        {
            "name": "bad_script",
            "executor": "script",
            "script": {"path": "../etc/passwd"},
            "agents": ["Developer"],
        }
    )
    assert not ok
    assert any("script.path" in e for e in errors)


def test_auto_forge_flutter_analyze(monkeypatch):
    initialize()
    state.REFINEMENT_MODE = False
    from backend.services.tool_forge_service import try_auto_forge_unknown_tool
    from backend.services.workflow_settings import get_workflow_settings, save_workflow_settings

    ws = get_workflow_settings()
    ws["autoForgeUnknownTools"] = True
    ws["customTools"] = []
    ws["enableSemanticSearch"] = False
    ws["enableWebSearch"] = False
    save_workflow_settings(ws)
    configure_agent_tools(ws)
    forged = try_auto_forge_unknown_tool("flutter_analyze", {}, agent_role="Developer")
    assert forged and forged.get("ok")
    assert "flutter_analyze" in agent_dev.registry.tool_names()
