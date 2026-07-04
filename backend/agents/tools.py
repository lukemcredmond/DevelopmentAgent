import json
from typing import Any, Callable, Dict, List


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
        if name not in self._tools:
            return f"Error: Tool '{name}' is not registered."
        try:
            result = self._tools[name].execute(**arguments)
            return json.dumps(result, indent=2) if isinstance(result, (dict, list)) else str(result)
        except Exception as e:
            return f"Error executing tool '{name}': {str(e)}"
