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
