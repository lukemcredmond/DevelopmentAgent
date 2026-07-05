"""Register MCP stdio server tools into agent ToolRegistry instances."""

import json
import subprocess
import threading
from typing import Any, Callable, Dict, List, Optional

from backend.agents.registry import agent_cr, agent_dev, agent_po, agent_qa
from backend.agents.tools import Tool
from backend.services.logs import add_system_log
from backend.services.workflow_settings import get_workflow_settings

_REGISTERED_MCP_TOOLS: List[str] = []
_MCP_TOOL_INSTANCES: List[Tool] = []
_MCP_CLIENTS: Dict[str, "_McpStdioClient"] = {}
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


def _make_mcp_tool_func(client: _McpStdioClient, tool_name: str) -> Callable:
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


def reregister_mcp_tools_on_agents() -> int:
    """Re-attach cached MCP tools after configure_agent_tools clears registries."""
    with _LOCK:
        if not _MCP_TOOL_INSTANCES:
            return 0
        for tool in _MCP_TOOL_INSTANCES:
            for agent in ALL_AGENTS:
                agent.registry.register(tool)
        return len(_MCP_TOOL_INSTANCES)


def register_mcp_tools_from_settings() -> int:
    """Connect configured MCP servers and register their tools on all agents."""
    clear_mcp_tools()
    servers = get_workflow_settings().get("mcpServers") or []
    if not isinstance(servers, list):
        return 0

    count = 0
    for spec in servers:
        if not isinstance(spec, dict):
            continue
        name = str(spec.get("name") or "mcp")
        transport = str(spec.get("transport") or "stdio")
        if transport != "stdio":
            add_system_log("System", "warning", f"MCP server '{name}': only stdio transport supported in v1")
            continue
        command = spec.get("command")
        if not command:
            continue
        args = spec.get("args") or []
        if not isinstance(args, list):
            args = []
        try:
            client = _McpStdioClient(name, str(command), [str(a) for a in args])
            _MCP_CLIENTS[name] = client
            for tool_def in client.list_tools():
                raw_name = str(tool_def.get("name") or "tool")
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
            add_system_log("System", "success", f"MCP '{name}': registered {len(client.list_tools())} tool(s)")
        except Exception as exc:
            add_system_log("System", "error", f"MCP server '{name}' failed: {exc}")

    return count
