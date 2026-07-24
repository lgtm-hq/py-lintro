"""Internal MCP tool registry for toolkit plugins."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from lintro.mcp.annotations import annotations_from_spec, tool_annotations_dict


@dataclass(frozen=True)
class McpToolSpec:
    """Specification for a single MCP tool.

    Attributes:
        name: Unique tool name (e.g. ``lintro_ping``).
        description: Human-readable tool description.
        input_schema: JSON Schema object for tool arguments.
        handler: Callable taking an arguments dict and returning a result.
        read_only: Maps to MCP ``readOnlyHint``.
        destructive: Maps to MCP ``destructiveHint``.
        idempotent: Maps to MCP ``idempotentHint``.
    """

    name: str
    description: str
    input_schema: dict[str, Any]
    handler: Callable[[dict[str, Any]], Any]
    read_only: bool = False
    destructive: bool = False
    idempotent: bool = False

    def to_annotations(self) -> dict[str, bool]:
        """Return MCP annotation hints for this tool.

        Returns:
            Dict with ``readOnlyHint``, ``destructiveHint``, ``idempotentHint``.
        """
        return annotations_from_spec(self)


class McpToolRegistry:
    """Collect and look up :class:`McpToolSpec` entries for the MCP server."""

    def __init__(self) -> None:
        """Create an empty registry."""
        self._tools: dict[str, McpToolSpec] = {}

    def register(self, spec: McpToolSpec) -> None:
        """Register a single tool specification.

        Args:
            spec: Tool to register.

        Raises:
            ValueError: If a tool with the same name is already registered.
        """
        if spec.name in self._tools:
            raise ValueError(f"MCP tool already registered: {spec.name}")
        self._tools[spec.name] = spec

    def register_toolkit(self, specs: Iterable[McpToolSpec]) -> None:
        """Register multiple tools from a toolkit.

        Args:
            specs: Tool specifications to register.
        """
        for spec in specs:
            self.register(spec)

    def get(self, name: str) -> McpToolSpec | None:
        """Look up a tool by name.

        Args:
            name: Tool name.

        Returns:
            The tool spec, or ``None`` if missing.
        """
        return self._tools.get(name)

    def list_tools(self) -> Sequence[McpToolSpec]:
        """Return registered tools in registration order.

        Returns:
            Sequence of tool specifications.
        """
        return tuple(self._tools.values())

    def __contains__(self, name: str) -> bool:
        """Return whether ``name`` is registered."""
        return name in self._tools

    def __len__(self) -> int:
        """Return the number of registered tools."""
        return len(self._tools)


__all__ = [
    "McpToolRegistry",
    "McpToolSpec",
    "tool_annotations_dict",
]
