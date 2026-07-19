"""Register MCP stdio/HTTP/SSE server tools into agent ToolRegistry instances."""

import json
import subprocess
import threading
import urllib.error
import urllib.request
from typing import Any, Callable, Dict, List, Optional, Set

from backend.agents.registry import agent_cr, agent_dev, agent_po, agent_qa
from backend.agents.tools import Tool
from backend.services.logs import add_system_log
from backend.services.workflow_settings import get_workflow_settings

_REGISTERED_MCP_TOOLS: List[str] = []
_MCP_TOOL_INSTANCES: List[Tool] = []
_MCP_CLIENTS: Dict[str, Any] = {}
_LOCK = threading.Lock()

ALL_AGENTS = [agent_po, agent_dev, agent_cr, agent_qa]


class _McpStdioClient:
    """Minimal MCP JSON-RPC client over stdio (Content-Length framing)."""

    def __init__(self, name: str, command: str, args: Optional[List[str]] = None):
        self.name = name
        self._proc = subprocess.Popen(
            [command, *(args or [])],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            bufsize=0,
        )
        self._next_id = 1
        self._initialize()

    def _send(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        if not self._proc.stdin or not self._proc.stdout:
            raise RuntimeError("MCP process not running")
        body = json.dumps(payload)
        framed = f"Content-Length: {len(body.encode('utf-8'))}\r\n\r\n{body}"
        self._proc.stdin.write(framed)
        self._proc.stdin.flush()
        return self._read_response()

    def _read_response(self) -> Dict[str, Any]:
        if not self._proc.stdout:
            raise RuntimeError("MCP process stdout unavailable")
        headers: Dict[str, str] = {}
        while True:
            line = self._proc.stdout.readline()
            if not line:
                raise RuntimeError("MCP server closed connection")
            if line in ("\r\n", "\n"):
                break
            if ":" in line:
                key, val = line.split(":", 1)
                headers[key.strip().lower()] = val.strip()
        length = int(headers.get("content-length", "0"))
        if length <= 0:
            return {}
        body = self._proc.stdout.read(length)
        return json.loads(body)

    def _initialize(self) -> None:
        init_id = self._next_id
        self._next_id += 1
        self._send(
            {
                "jsonrpc": "2.0",
                "id": init_id,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "allhands", "version": "1.0"},
                },
            }
        )
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})

    def list_tools(self) -> List[Dict[str, Any]]:
        req_id = self._next_id
        self._next_id += 1
        resp = self._send({"jsonrpc": "2.0", "id": req_id, "method": "tools/list", "params": {}})
        result = resp.get("result") or {}
        return list(result.get("tools") or [])

    def call_tool(self, name: str, arguments: Dict[str, Any]) -> str:
        req_id = self._next_id
        self._next_id += 1
        resp = self._send(
            {
                "jsonrpc": "2.0",
                "id": req_id,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            }
        )
        if resp.get("error"):
            return f"Error: MCP tool call failed — {resp['error']}"
        result = resp.get("result") or {}
        content = result.get("content") or []
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("text"):
                parts.append(str(block["text"]))
        return "\n".join(parts) if parts else json.dumps(result, indent=2)

    def close(self) -> None:
        try:
            if self._proc.stdin:
                self._proc.stdin.close()
            self._proc.terminate()
            self._proc.wait(timeout=3)
        except Exception:
            try:
                self._proc.kill()
            except Exception:
                pass


class _McpHttpClient:
    """Minimal MCP JSON-RPC over HTTP POST (streamable HTTP / SSE-compatible servers)."""

    def __init__(self, name: str, url: str, headers: Optional[Dict[str, str]] = None):
        self.name = name
        self.url = url.rstrip("/")
        self.headers = {"Content-Type": "application/json", **(headers or {})}
        self._next_id = 1
        self._initialize()

    def _post(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(self.url, data=data, headers=self.headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw) if raw.strip() else {}
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"HTTP {exc.code}: {body[:300]}") from exc

    def _initialize(self) -> None:
        init_id = self._next_id
        self._next_id += 1
        self._post(
            {
                "jsonrpc": "2.0",
                "id": init_id,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "allhands", "version": "1.0"},
                },
            }
        )
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})

    def list_tools(self) -> List[Dict[str, Any]]:
        req_id = self._next_id
        self._next_id += 1
        resp = self._post({"jsonrpc": "2.0", "id": req_id, "method": "tools/list", "params": {}})
        result = resp.get("result") or {}
        return list(result.get("tools") or [])

    def call_tool(self, name: str, arguments: Dict[str, Any]) -> str:
        req_id = self._next_id
        self._next_id += 1
        resp = self._post(
            {
                "jsonrpc": "2.0",
                "id": req_id,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            }
        )
        if resp.get("error"):
            return f"Error: MCP tool call failed — {resp['error']}"
        result = resp.get("result") or {}
        content = result.get("content") or []
        parts = [str(b.get("text")) for b in content if isinstance(b, dict) and b.get("text")]
        return "\n".join(parts) if parts else json.dumps(result, indent=2)

    def close(self) -> None:
        return


def _tool_enabled(raw_name: str, spec: Dict[str, Any]) -> bool:
    enabled = spec.get("enabledTools") or spec.get("enabled_tools")
    disabled = spec.get("disabledTools") or spec.get("disabled_tools")
    if isinstance(disabled, list) and raw_name in disabled:
        return False
    if isinstance(enabled, list) and enabled:
        return raw_name in enabled
    if spec.get("enabled") is False:
        return False
    return True


def _make_mcp_tool_func(client: Any, tool_name: str) -> Callable:
    def _run(**kwargs: Any) -> str:
        return client.call_tool(tool_name, kwargs)

    return _run


def clear_mcp_tools() -> None:
    with _LOCK:
        for agent in ALL_AGENTS:
            for name in list(_REGISTERED_MCP_TOOLS):
                agent.registry._tools.pop(name, None)
        _REGISTERED_MCP_TOOLS.clear()
        _MCP_TOOL_INSTANCES.clear()
        for client in _MCP_CLIENTS.values():
            client.close()
        _MCP_CLIENTS.clear()


def reregister_mcp_tools_on_agents(agent_tools_cfg: Optional[Dict[str, Any]] = None) -> int:
    """Re-attach cached MCP tools after configure_agent_tools clears registries.

    If agent_tools_cfg has a non-empty allowlist for an agent, only attach MCP tools
    whose names appear on that list (or if the list contains a bare 'mcp_*' / 'mcp' token,
    attach all MCP tools for that agent).
    """
    role_by_agent = {
        agent_po: "Product Owner",
        agent_dev: "Developer",
        agent_cr: "Code Reviewer",
        agent_qa: "QA Tester",
    }
    cfg = agent_tools_cfg if isinstance(agent_tools_cfg, dict) else {}

    with _LOCK:
        if not _MCP_TOOL_INSTANCES:
            return 0
        for tool in _MCP_TOOL_INSTANCES:
            for agent in ALL_AGENTS:
                role = role_by_agent.get(agent, "")
                override = cfg.get(role)
                if isinstance(override, list) and override:
                    names = {str(n) for n in override}
                    allow_all_mcp = "mcp" in names or "mcp_*" in names or "*" in names
                    if not allow_all_mcp and tool.name not in names:
                        continue
                agent.registry.register(tool)
        return len(_MCP_TOOL_INSTANCES)


def register_mcp_tools_from_settings() -> int:
    """Connect configured MCP servers and register their tools on all agents."""
    clear_mcp_tools()
    servers = get_workflow_settings().get("mcpServers") or []
    if not isinstance(servers, list):
        return 0

    max_tools = int(get_workflow_settings().get("maxMcpTools") or 40)
    count = 0
    for spec in servers:
        if not isinstance(spec, dict):
            continue
        if count >= max_tools:
            add_system_log("System", "warning", f"MCP tool budget ({max_tools}) reached — skipping remaining servers")
            break
        name = str(spec.get("name") or "mcp")
        transport = str(spec.get("transport") or "stdio").lower()
        try:
            if transport in ("http", "sse", "streamable_http", "streamable-http"):
                url = spec.get("url") or spec.get("endpoint")
                if not url:
                    add_system_log("System", "warning", f"MCP server '{name}': missing url for {transport}")
                    continue
                headers = spec.get("headers") if isinstance(spec.get("headers"), dict) else {}
                client = _McpHttpClient(name, str(url), headers=headers)
            elif transport == "stdio":
                command = spec.get("command")
                if not command:
                    continue
                args = spec.get("args") or []
                if not isinstance(args, list):
                    args = []
                client = _McpStdioClient(name, str(command), [str(a) for a in args])
            else:
                add_system_log("System", "warning", f"MCP server '{name}': unsupported transport '{transport}'")
                continue
            _MCP_CLIENTS[name] = client
            registered = 0
            for tool_def in client.list_tools():
                if count >= max_tools:
                    break
                raw_name = str(tool_def.get("name") or "tool")
                if not _tool_enabled(raw_name, spec):
                    continue
                reg_name = f"mcp_{name}_{raw_name}".replace("-", "_").replace(".", "_")
                schema = tool_def.get("inputSchema") or {
                    "type": "object",
                    "properties": {},
                    "required": [],
                }
                tool = Tool(
                    name=reg_name,
                    description=str(tool_def.get("description") or f"MCP tool {raw_name} from {name}"),
                    parameters=schema,
                    func=_make_mcp_tool_func(client, raw_name),
                )
                for agent in ALL_AGENTS:
                    agent.register_tool(tool)
                _REGISTERED_MCP_TOOLS.append(reg_name)
                _MCP_TOOL_INSTANCES.append(tool)
                count += 1
                registered += 1
            add_system_log("System", "success", f"MCP '{name}' ({transport}): registered {registered} tool(s)")
        except Exception as exc:
            add_system_log("System", "error", f"MCP server '{name}' failed: {exc}")

    return count
