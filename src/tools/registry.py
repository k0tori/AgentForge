from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolDefinition:
    """A registered tool with its metadata and annotations."""

    name: str
    description: str
    func: Callable[..., Any]
    annotations: dict[str, bool] = field(default_factory=dict)

    def to_langchain_tool(self) -> dict:
        """Convert to LangChain tool format for function calling."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self._infer_parameters(),
            },
        }

    def _infer_parameters(self) -> dict:
        """Infer JSON schema from function signature."""
        import inspect

        sig = inspect.signature(self.func)
        properties = {}
        required = []

        for param_name, param in sig.parameters.items():
            if param_name in ("self", "cls"):
                continue
            param_type = "string"  # default
            if param.annotation != inspect.Parameter.empty:
                type_map = {str: "string", int: "integer", float: "number", bool: "boolean"}
                param_type = type_map.get(param.annotation, "string")

            prop: dict[str, Any] = {"type": param_type}
            if param.default != inspect.Parameter.empty:
                prop["default"] = param.default
            else:
                required.append(param_name)

            properties[param_name] = prop

        return {"type": "object", "properties": properties, "required": required}


class ToolRegistry:
    """Central registry for all tools available to agents."""

    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}

    def register(
        self,
        name: str,
        func: Callable,
        description: str,
        annotations: dict[str, bool] | None = None,
    ) -> None:
        """Register a tool with its annotations."""
        self._tools[name] = ToolDefinition(
            name=name,
            func=func,
            description=description,
            annotations=annotations or {},
        )

    def get_tool(self, name: str) -> ToolDefinition | None:
        """Get a tool by name."""
        return self._tools.get(name)

    def get_all_tools(self) -> list[ToolDefinition]:
        """Get all registered tools."""
        return list(self._tools.values())

    def get_tool_names(self) -> list[str]:
        """Get all registered tool names."""
        return list(self._tools.keys())

    def get_tools_by_annotation(self, key: str, value: bool) -> list[ToolDefinition]:
        """Get tools filtered by annotation value."""
        return [t for t in self._tools.values() if t.annotations.get(key) == value]

    def to_langchain_tools(self) -> list[dict]:
        """Export all tools in LangChain format."""
        return [t.to_langchain_tool() for t in self._tools.values()]

    async def execute(self, name: str, **kwargs) -> Any:
        """Execute a tool by name."""
        tool = self.get_tool(name)
        if tool is None:
            raise ValueError(f"Tool '{name}' not found")
        return tool.func(**kwargs)


# Global registry instance
registry = ToolRegistry()
