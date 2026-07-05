import json
from typing import Any, Callable, Dict, List

from backend import state


class Tool:
    """Encapsulates a standard execute function with model definitions."""

    def __init__(
        self,
        name: str,
        description: str,
        parameters: Dict[str, Any],
        func: Callable,
    ):
        self.name = name
        self.description = description
        self.parameters = parameters
        self.func = func

    def execute(self, **kwargs) -> Any:
        return self.func(**kwargs)


class ToolRegistry:
    """Manages system tools for dynamic registration and invocation."""

    def __init__(self) -> None:
        self._tools: Dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def clear(self) -> None:
        self._tools.clear()

    def tool_names(self) -> List[str]:
        return list(self._tools.keys())

    def get_definitions(self) -> List[Dict[str, Any]]:
        return [
            {
                "name": t.name,
                "description": t.description,
                "parameters": t.parameters,
            }
            for t in self._tools.values()
        ]

    def get_ollama_tools(self) -> List[Dict[str, Any]]:
        """OpenAI-style tool schemas for Ollama native tool calling."""
        return [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.parameters,
                },
            }
            for t in self._tools.values()
        ]

    def invoke(self, name: str, arguments: Dict[str, Any]) -> str:
        from backend.services.tool_aliases import queue_pending_tool, resolve_tool_call

        original_name = name
        resolved_name, resolved_args, was_alias = resolve_tool_call(name, arguments)
        name = resolved_name
        arguments = resolved_args

        if name not in self._tools:
            queue_pending_tool(
                original_name,
                arguments,
                task_id=state.ACTIVE_SPRINT_TASK_ID,
                agent_role=state.ACTIVE_SPRINT_AGENT,
            )
            return (
                f"Error: Tool '{original_name}' is not registered. "
                "Map it in the Tool Resolution dialog."
            )
        try:
            result = self._tools[name].execute(**arguments)
            return json.dumps(result, indent=2) if isinstance(result, (dict, list)) else str(result)
        except Exception as e:
            return f"Error executing tool '{name}': {str(e)}"
