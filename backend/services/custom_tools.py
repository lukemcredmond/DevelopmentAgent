"""User-defined custom tools (shell / http / sql / script) registered onto agent registries."""

from __future__ import annotations

import json
import os
import re
import shlex
import sqlite3
import subprocess
import urllib.error
import urllib.request
from typing import Any, Callable, Dict, List, Optional, Set
from urllib.parse import unquote, urlparse

from backend.agents.tools import Tool

MAX_CUSTOM_TOOL_OUTPUT_CHARS = 50_000

_DESTRUCTIVE_SHELL_MARKERS = (
    "rm -rf",
    "rm -fr",
    "mkfs.",
    "dd if=",
    ":(){ :|:& };:",
)

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


def resolve_workspace_relative_path(rel_path: str) -> Optional[str]:
    """Resolve a workspace-relative path; reject traversal and paths outside WORKSPACE_DIR."""
    from backend import state

    rel = (rel_path or "").strip().replace("\\", "/")
    if not rel or rel.startswith("/") or ".." in rel.split("/"):
        return None
    ws = os.path.normpath(state.WORKSPACE_DIR or ".")
    abs_path = os.path.normpath(os.path.join(ws, rel))
    if abs_path != ws and not abs_path.startswith(ws + os.sep):
        return None
    return abs_path


def _shell_post_process_cfg(tool_def: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    top = tool_def.get("shellPostProcess")
    if isinstance(top, dict) and top:
        return top
    shell_cfg = tool_def.get("shell") if isinstance(tool_def.get("shell"), dict) else {}
    nested = shell_cfg.get("postProcess")
    if isinstance(nested, dict) and nested:
        return nested
    return None


def _diagnostics_by_builtin(builtin: str, output: str) -> List[Dict[str, Any]]:
    from backend.services.diagnostics_parser import (
        parse_dart_analyze,
        parse_eslint_output,
        parse_generic,
        parse_pytest_failures,
        parse_tsc_output,
    )

    key = (builtin or "").strip().lower().replace("-", "_")
    out = output or ""
    if key in ("dart_analyze", "flutter_analyze"):
        return parse_dart_analyze(out)
    if key == "tsc":
        return parse_tsc_output(out)
    if key == "eslint":
        return parse_eslint_output(out)
    if key == "pytest":
        return parse_pytest_failures(out)
    if key == "generic":
        return parse_generic(out)
    return parse_dart_analyze(out) + parse_generic(out)


def _run_workspace_script_pipe(
    script_rel: str,
    *,
    stdin_text: str,
    interpreter: str = "python3",
    timeout_sec: int = 120,
) -> str:
    path = resolve_workspace_relative_path(script_rel)
    if not path or not os.path.isfile(path):
        return f"Error: post-process script not found at '{script_rel}'"
    try:
        proc = subprocess.run(
            [interpreter or "python3", path],
            input=(stdin_text or "").encode("utf-8"),
            capture_output=True,
            timeout=max(5, int(timeout_sec)),
            cwd=os.path.dirname(path) or ".",
        )
    except subprocess.TimeoutExpired:
        return f"Error: script timed out after {timeout_sec}s"
    except Exception as e:
        return f"Error running script: {e}"
    combined = (proc.stdout or b"").decode("utf-8", errors="replace")
    if proc.stderr:
        err = proc.stderr.decode("utf-8", errors="replace")
        if err.strip():
            combined = (combined + "\n" + err).strip()
    if len(combined) > MAX_CUSTOM_TOOL_OUTPUT_CHARS:
        combined = combined[:MAX_CUSTOM_TOOL_OUTPUT_CHARS] + "\n…(truncated)"
    if proc.returncode != 0 and not combined.strip():
        return f"Error: script exit {proc.returncode}"
    return combined or f"(script exit {proc.returncode}, no output)"


def _apply_shell_post_process(command: str, raw_output: str, post_cfg: Dict[str, Any]) -> str:
    from backend.agents.tool_outcomes import classify_run_command_outcome
    from backend.services.command_result import CommandResult, format_command_result_for_agent
    from backend.services.diagnostics_parser import summarize_diagnostics

    script_cfg = post_cfg.get("script") if isinstance(post_cfg.get("script"), dict) else {}
    script_path = post_cfg.get("path") or script_cfg.get("path")
    if script_path:
        processed = _run_workspace_script_pipe(
            str(script_path),
            stdin_text=raw_output,
            interpreter=str(script_cfg.get("interpreter") or post_cfg.get("interpreter") or "python3"),
            timeout_sec=int(script_cfg.get("timeoutSec") or post_cfg.get("timeoutSec") or 120),
        )
        if processed.startswith("Error:"):
            return processed
        raw_output = processed

    builtin = post_cfg.get("builtin")
    if builtin:
        findings = _diagnostics_by_builtin(str(builtin), raw_output)
        outcome = classify_run_command_outcome(command, 0, raw_output, findings)
        summary = summarize_diagnostics(findings)
        result = CommandResult(
            command=command,
            exit_code=0,
            stdout=raw_output,
            stderr="",
            duration_ms=0,
            outcome=outcome,
            diagnostics=findings,
            summary=summary,
        )
        return format_command_result_for_agent(result)

    if len(raw_output) > MAX_CUSTOM_TOOL_OUTPUT_CHARS:
        raw_output = raw_output[:MAX_CUSTOM_TOOL_OUTPUT_CHARS] + "\n…(truncated)"
    return raw_output


def execute_script_tool(tool_def: Dict[str, Any], **kwargs: Any) -> str:
    script_cfg = tool_def.get("script") if isinstance(tool_def.get("script"), dict) else {}
    rel = str(script_cfg.get("path") or "").strip()
    path = resolve_workspace_relative_path(rel)
    if not path or not os.path.isfile(path):
        return f"Error: script not found at '{rel or '(empty path)'}'"
    interpreter = str(script_cfg.get("interpreter") or "python3")
    timeout = int(script_cfg.get("timeoutSec") or 120)
    from backend import state

    try:
        proc = subprocess.run(
            [interpreter, path],
            input=json.dumps(kwargs, default=str).encode("utf-8"),
            capture_output=True,
            timeout=max(5, timeout),
            cwd=state.WORKSPACE_DIR or ".",
        )
    except subprocess.TimeoutExpired:
        return f"Error: script timed out after {timeout}s"
    except Exception as e:
        return f"Error running script: {e}"
    out = (proc.stdout or b"").decode("utf-8", errors="replace")
    err = (proc.stderr or b"").decode("utf-8", errors="replace")
    if err.strip() and proc.returncode != 0:
        out = (out + "\n" + err).strip() if out.strip() else err.strip()
    if len(out) > MAX_CUSTOM_TOOL_OUTPUT_CHARS:
        out = out[:MAX_CUSTOM_TOOL_OUTPUT_CHARS] + "\n…(truncated)"
    if proc.returncode != 0 and not out:
        return f"Error: script exit {proc.returncode}"
    return out or f"(script exit {proc.returncode}, no output)"


def execute_shell_tool(tool_def: Dict[str, Any], **kwargs: Any) -> str:
    shell_cfg = tool_def.get("shell") if isinstance(tool_def.get("shell"), dict) else {}
    template = str(shell_cfg.get("command") or "").strip()
    if not template:
        return "Error: custom tool shell.command is empty"
    command = _format_shell_command(template, kwargs)
    post_cfg = _shell_post_process_cfg(tool_def)
    if post_cfg:
        from backend.services.command_result import run_workspace_command

        result = run_workspace_command(command)
        combined = result.combined_output
        return _apply_shell_post_process(command, combined, post_cfg)

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
    if executor == "script":
        return execute_script_tool(tool_def, **kwargs)
    return f"Error: Unknown custom tool executor '{executor}'"


def shell_command_is_safe(template: str) -> bool:
    lower = (template or "").lower()
    return not any(marker in lower for marker in _DESTRUCTIVE_SHELL_MARKERS)


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
    shell_raw = raw.get("shell") if isinstance(raw.get("shell"), dict) else {}
    shell_post = raw.get("shellPostProcess")
    if not isinstance(shell_post, dict):
        shell_post = shell_raw.get("postProcess") if isinstance(shell_raw.get("postProcess"), dict) else {}
    script_raw = raw.get("script") if isinstance(raw.get("script"), dict) else {}
    return {
        "id": str(raw.get("id") or name),
        "name": name,
        "description": str(raw.get("description") or f"Custom tool {name}"),
        "parameters": params,
        "agents": agents,
        "executor": str(raw.get("executor") or "shell").lower(),
        "shell": shell_raw,
        "shellPostProcess": shell_post if isinstance(shell_post, dict) else {},
        "http": raw.get("http") if isinstance(raw.get("http"), dict) else {},
        "sql": raw.get("sql") if isinstance(raw.get("sql"), dict) else {},
        "script": script_raw,
    }


def list_project_custom_tool_defs(settings: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """Project-scoped custom tools only (no global merge)."""
    from backend.services.workflow_settings import get_workflow_settings

    ws = settings if settings is not None else get_workflow_settings()
    out: List[Dict[str, Any]] = []
    for raw in ws.get("customTools") or []:
        if isinstance(raw, dict):
            norm = normalize_custom_tool_def(raw)
            if norm:
                out.append({**norm, "scope": "project"})
    return out


GLOBAL_CUSTOM_TOOLS_KEY = "global_custom_tools"


def load_global_custom_tools() -> List[Dict[str, Any]]:
    """Load global custom tool defs from ProjectStorage (survives project switches)."""
    from backend import state

    raw = state.storage.get_setting(GLOBAL_CUSTOM_TOOLS_KEY)
    if not raw:
        return []
    try:
        import json

        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(data, list):
        return []
    out: List[Dict[str, Any]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        norm = normalize_custom_tool_def(item)
        if norm:
            out.append({**norm, "scope": "global"})
    return out


def save_global_custom_tools(defs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Normalize and persist global custom tools; returns saved defs with scope."""
    import json

    from backend import state

    saved: List[Dict[str, Any]] = []
    for raw in defs or []:
        if not isinstance(raw, dict):
            continue
        norm = normalize_custom_tool_def(raw)
        if norm:
            saved.append({**norm, "scope": "global"})
    # Persist without scope field noise (re-added on load)
    persist = [{k: v for k, v in d.items() if k != "scope"} for d in saved]
    state.storage.set_setting(GLOBAL_CUSTOM_TOOLS_KEY, json.dumps(persist))
    return saved


def merged_custom_tool_defs(settings: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """Global first, then project — same name → project wins."""
    by_name: Dict[str, Dict[str, Any]] = {}
    for d in load_global_custom_tools():
        by_name[d["name"]] = {**d, "scope": "global"}
    for d in list_project_custom_tool_defs(settings):
        by_name[d["name"]] = {**d, "scope": "project"}
    return list(by_name.values())


def build_custom_tools(settings: Optional[Dict[str, Any]] = None) -> List[Tool]:
    """Build Tool instances from merged global + project customTools."""
    defs = merged_custom_tool_defs(settings)
    tools: List[Tool] = []
    for norm in defs:
        # Executor closes over def without scope
        exec_def = {k: v for k, v in norm.items() if k != "scope"}
        tools.append(
            Tool(
                name=norm["name"],
                description=norm["description"],
                parameters=norm["parameters"],
                func=_make_executor(exec_def),
            )
        )
    sync_custom_canonical_names(defs)
    return tools


def custom_tools_for_agent(role: str, settings: Optional[Dict[str, Any]] = None) -> List[Tool]:
    all_tools = build_custom_tools(settings)
    allowed_names: Set[str] = set()
    for norm in merged_custom_tool_defs(settings):
        agents = norm.get("agents") or []
        if role in agents or any(str(a).lower() == "all" for a in agents):
            allowed_names.add(norm["name"])
    return [t for t in all_tools if t.name in allowed_names]


def list_custom_tool_defs(settings: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """Merged custom tool defs with scope for catalog/UI."""
    return merged_custom_tool_defs(settings)
