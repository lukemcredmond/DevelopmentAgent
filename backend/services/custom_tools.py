"""User-defined custom tools (shell / http / sql) registered onto agent registries."""

from __future__ import annotations

import json
import re
import shlex
import sqlite3
import urllib.error
import urllib.request
from typing import Any, Callable, Dict, List, Optional, Set
from urllib.parse import unquote, urlparse

from backend.agents.tools import Tool

_CUSTOM_CANONICAL_NAMES: Set[str] = set()

QUERY_SQL_PRESET: Dict[str, Any] = {
    "id": "query_sql",
    "name": "query_sql",
    "description": "Run a read-only SQL query against a named database connection.",
    "parameters": {
        "type": "object",
        "properties": {
            "db_name": {"type": "string", "description": "Connection key from tool sql.connections"},
            "query": {"type": "string", "description": "SQL SELECT/WITH query"},
        },
        "required": ["db_name", "query"],
    },
    "agents": ["Developer", "QA Tester"],
    "executor": "sql",
    "sql": {
        "connections": {"local": "sqlite:///./data/app.db"},
        "readOnly": True,
        "maxRows": 200,
    },
}


def get_custom_canonical_names() -> Set[str]:
    return set(_CUSTOM_CANONICAL_NAMES)


def sync_custom_canonical_names(defs: List[Dict[str, Any]]) -> None:
    _CUSTOM_CANONICAL_NAMES.clear()
    for d in defs:
        if isinstance(d, dict) and d.get("name"):
            _CUSTOM_CANONICAL_NAMES.add(str(d["name"]))


def _format_shell_command(template: str, kwargs: Dict[str, Any]) -> str:
    """Replace {param} placeholders with shell-quoted values."""

    def repl(match: re.Match[str]) -> str:
        key = match.group(1)
        val = kwargs.get(key, "")
        return shlex.quote(str(val))

    return re.sub(r"\{(\w+)\}", repl, template)


def _is_readonly_sql(query: str) -> bool:
    stripped = query.strip().lstrip("(").strip()
    if not stripped:
        return False
    first = stripped.split(None, 1)[0].upper()
    return first in ("SELECT", "WITH", "PRAGMA", "EXPLAIN")


def _sqlite_path_from_url(url: str) -> str:
    """Parse sqlite:///relative/path or sqlite:////abs/path."""
    if url.startswith("sqlite:///"):
        rest = url[len("sqlite:///") :]
        if rest.startswith("/"):
            return rest  # absolute on unix-like; on Windows rare
        return rest
    parsed = urlparse(url)
    if parsed.scheme == "sqlite":
        path = unquote(parsed.path or "")
        if path.startswith("/") and len(path) > 2 and path[2] == ":":
            # /C:/...
            return path[1:]
        return path.lstrip("/") if not path.startswith("/") else path
    return url


def execute_sql_tool(tool_def: Dict[str, Any], **kwargs: Any) -> str:
    sql_cfg = tool_def.get("sql") if isinstance(tool_def.get("sql"), dict) else {}
    connections = sql_cfg.get("connections") if isinstance(sql_cfg.get("connections"), dict) else {}
    read_only = sql_cfg.get("readOnly", True) is not False
    max_rows = int(sql_cfg.get("maxRows") or 200)

    db_name = str(kwargs.get("db_name") or kwargs.get("dbName") or "").strip()
    query = str(kwargs.get("query") or "").strip()
    if not db_name or not query:
        return "Error: db_name and query are required"

    conn_url = connections.get(db_name)
    if not conn_url:
        known = ", ".join(sorted(connections.keys())) or "(none)"
        return f"Error: Unknown db_name '{db_name}'. Known: {known}"

    if read_only and not _is_readonly_sql(query):
        return "Error: Only read-only SELECT/WITH queries are allowed for this tool"

    url = str(conn_url)
    if not url.startswith("sqlite"):
        return (
            f"Error: Connection '{db_name}' uses unsupported scheme. "
            "Currently only sqlite:/// paths are supported in-process."
        )

    from backend import state
    import os

    db_path = _sqlite_path_from_url(url)
    if not os.path.isabs(db_path):
        db_path = os.path.normpath(os.path.join(state.WORKSPACE_DIR, db_path))

    if not os.path.exists(db_path):
        return f"Error: SQLite database not found at '{db_path}'"

    try:
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.execute(query)
            rows = cur.fetchmany(max_rows + 1)
            truncated = len(rows) > max_rows
            rows = rows[:max_rows]
            data = [dict(r) for r in rows]
            return json.dumps(
                {"rows": data, "count": len(data), "truncated": truncated},
                indent=2,
                default=str,
            )
    except Exception as e:
        return f"Error executing SQL: {e}"


def execute_shell_tool(tool_def: Dict[str, Any], **kwargs: Any) -> str:
    shell_cfg = tool_def.get("shell") if isinstance(tool_def.get("shell"), dict) else {}
    template = str(shell_cfg.get("command") or "").strip()
    if not template:
        return "Error: custom tool shell.command is empty"
    command = _format_shell_command(template, kwargs)
    from backend.workspace.files import run_agent_command

    return str(run_agent_command(command, background=False))


def execute_http_tool(tool_def: Dict[str, Any], **kwargs: Any) -> str:
    http_cfg = tool_def.get("http") if isinstance(tool_def.get("http"), dict) else {}
    url = str(http_cfg.get("url") or "").strip()
    if not url:
        return "Error: custom tool http.url is empty"
    method = str(http_cfg.get("method") or "POST").upper()
    timeout = float(http_cfg.get("timeoutSec") or 30)
    headers = http_cfg.get("headers") if isinstance(http_cfg.get("headers"), dict) else {}
    body = json.dumps(kwargs).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body if method in ("POST", "PUT", "PATCH") else None,
        method=method,
        headers={"Content-Type": "application/json", **{str(k): str(v) for k, v in headers.items()}},
    )
    if method == "GET" and kwargs:
        # Append query string for GET
        from urllib.parse import urlencode, urlparse, urlunparse, parse_qs

        parsed = urlparse(url)
        q = parse_qs(parsed.query)
        for k, v in kwargs.items():
            q[str(k)] = [str(v)]
        flat = {k: v[0] if len(v) == 1 else v for k, v in q.items()}
        # urlencode needs scalar values
        qs = urlencode({k: (v if isinstance(v, str) else json.dumps(v)) for k, v in flat.items()})
        url = urlunparse(parsed._replace(query=qs))
        req = urllib.request.Request(
            url,
            method="GET",
            headers={str(k): str(v) for k, v in headers.items()},
        )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            text = resp.read().decode("utf-8", errors="replace")
            if len(text) > 50_000:
                text = text[:50_000] + "\n…(truncated)"
            return text
    except urllib.error.HTTPError as e:
        body_err = e.read().decode("utf-8", errors="replace")[:2000]
        return f"Error HTTP {e.code}: {body_err}"
    except Exception as e:
        return f"Error HTTP request: {e}"


def execute_custom_tool(tool_def: Dict[str, Any], **kwargs: Any) -> str:
    executor = str(tool_def.get("executor") or "shell").lower()
    if executor == "sql":
        return execute_sql_tool(tool_def, **kwargs)
    if executor == "http":
        return execute_http_tool(tool_def, **kwargs)
    if executor == "shell":
        return execute_shell_tool(tool_def, **kwargs)
    return f"Error: Unknown custom tool executor '{executor}'"


def _make_executor(tool_def: Dict[str, Any]) -> Callable[..., str]:
    def _fn(**kwargs: Any) -> str:
        return execute_custom_tool(tool_def, **kwargs)

    return _fn


def normalize_custom_tool_def(raw: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not isinstance(raw, dict):
        return None
    name = str(raw.get("name") or "").strip()
    if not name or not re.match(r"^[a-zA-Z_][a-zA-Z0-9_]*$", name):
        return None
    params = raw.get("parameters")
    if not isinstance(params, dict):
        params = {"type": "object", "properties": {}, "required": []}
    agents = raw.get("agents")
    if not isinstance(agents, list):
        agents = ["Developer"]
    agents = [str(a) for a in agents]
    return {
        "id": str(raw.get("id") or name),
        "name": name,
        "description": str(raw.get("description") or f"Custom tool {name}"),
        "parameters": params,
        "agents": agents,
        "executor": str(raw.get("executor") or "shell").lower(),
        "shell": raw.get("shell") if isinstance(raw.get("shell"), dict) else {},
        "http": raw.get("http") if isinstance(raw.get("http"), dict) else {},
        "sql": raw.get("sql") if isinstance(raw.get("sql"), dict) else {},
    }


def build_custom_tools(settings: Optional[Dict[str, Any]] = None) -> List[Tool]:
    """Build Tool instances from workflow settings customTools."""
    from backend.services.workflow_settings import get_workflow_settings

    ws = settings if settings is not None else get_workflow_settings()
    raw_list = ws.get("customTools") or []
    if not isinstance(raw_list, list):
        raw_list = []

    tools: List[Tool] = []
    defs: List[Dict[str, Any]] = []
    for raw in raw_list:
        norm = normalize_custom_tool_def(raw) if isinstance(raw, dict) else None
        if not norm:
            continue
        defs.append(norm)
        tools.append(
            Tool(
                name=norm["name"],
                description=norm["description"],
                parameters=norm["parameters"],
                func=_make_executor(norm),
            )
        )
    sync_custom_canonical_names(defs)
    return tools


def custom_tools_for_agent(role: str, settings: Optional[Dict[str, Any]] = None) -> List[Tool]:
    from backend.services.workflow_settings import get_workflow_settings

    ws = settings if settings is not None else get_workflow_settings()
    all_tools = build_custom_tools(ws)
    # Rebuild defs for agent filter
    raw_list = ws.get("customTools") or []
    allowed_names: Set[str] = set()
    for raw in raw_list if isinstance(raw_list, list) else []:
        norm = normalize_custom_tool_def(raw) if isinstance(raw, dict) else None
        if not norm:
            continue
        if role in norm["agents"] or "all" in [a.lower() for a in norm["agents"]]:
            allowed_names.add(norm["name"])
    return [t for t in all_tools if t.name in allowed_names]


def list_custom_tool_defs(settings: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    from backend.services.workflow_settings import get_workflow_settings

    ws = settings if settings is not None else get_workflow_settings()
    out: List[Dict[str, Any]] = []
    for raw in ws.get("customTools") or []:
        if isinstance(raw, dict):
            norm = normalize_custom_tool_def(raw)
            if norm:
                out.append(norm)
    return out
