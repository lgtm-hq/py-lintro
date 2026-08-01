"""Internal MCP tool registry for toolkit plugins."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from lintro.mcp.annotations import annotations_from_spec, tool_annotations_dict

__all__ = [
    "McpToolRegistry",
    "McpToolSpec",
    "tool_annotations_dict",
]


@dataclass(frozen=True)
class McpToolSpec:
    """Specification for a single MCP tool.

    Attributes:
        name: Unique tool name (e.g. ``lintro_ping``).
        description: Human-readable tool description.
        input_schema: JSON Schema object for tool arguments. Must be an object
            schema so the server can validate arguments before dispatch.
        handler: Callable taking an arguments dict and returning a result. May
            be a coroutine function.
        read_only: Maps to MCP ``readOnlyHint``.
        destructive: Maps to MCP ``destructiveHint``.
        idempotent: Maps to MCP ``idempotentHint``.
        path_arguments: Names of ``input_schema`` properties whose values are
            filesystem paths. The server resolves each one against the
            workspace root and rejects escapes *before* the handler runs, so a
            toolkit cannot forget the boundary check. Each named property may
            hold a single path string or an array of path strings.
    """

    name: str
    description: str
    input_schema: dict[str, Any]
    handler: Callable[[dict[str, Any]], Any]
    read_only: bool = False
    destructive: bool = False
    idempotent: bool = False
    path_arguments: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """Validate the specification at construction time.

        Raises:
            ValueError: If the name is empty, the input schema is not a JSON
                Schema object, or a declared path argument is absent from the
                schema properties.
        """
        if not self.name.strip():
            raise ValueError("MCP tool name must be a non-empty string")
        if self.input_schema.get("type") != "object":
            raise ValueError(
                f"MCP tool {self.name!r} input_schema must be a JSON Schema "
                'object (\'"type": "object"\')',
            )
        properties = self.input_schema.get("properties") or {}
        unknown = [key for key in self.path_arguments if key not in properties]
        if unknown:
            raise ValueError(
                f"MCP tool {self.name!r} declares path_arguments not present in "
                f"input_schema properties: {sorted(unknown)}",
            )

    def to_annotations(self) -> dict[str, bool]:
        """Return MCP annotation hints for this tool.

        Returns:
            Dict with ``readOnlyHint``, ``destructiveHint``, ``idempotentHint``.
        """
        return annotations_from_spec(spec=self)


class McpToolRegistry:
    """Collect and look up :class:`McpToolSpec` entries for the MCP server."""

    def __init__(self) -> None:
        """Create an empty registry."""
        self._tools: dict[str, McpToolSpec] = {}

    def register(self, *, spec: McpToolSpec) -> None:
        """Register a single tool specification.

        Args:
            spec: Tool to register.

        Raises:
            ValueError: If a tool with the same name is already registered.
        """
        if spec.name in self._tools:
            raise ValueError(f"MCP tool already registered: {spec.name}")
        self._tools[spec.name] = spec

    def register_toolkit(self, *, specs: Iterable[McpToolSpec]) -> None:
        """Register multiple tools from a toolkit.

        Args:
            specs: Tool specifications to register.
        """
        for spec in specs:
            self.register(spec=spec)

    def get(self, *, name: str) -> McpToolSpec | None:
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

    def __contains__(self, name: object) -> bool:
        """Return whether ``name`` is registered.

        Args:
            name: Candidate tool name.

        Returns:
            True when a tool with that name is registered.
        """
        return name in self._tools

    def __len__(self) -> int:
        """Return the number of registered tools.

        Returns:
            Count of registered tools.
        """
        return len(self._tools)
