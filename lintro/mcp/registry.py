"""Internal MCP tool registry for toolkit plugins."""

from __future__ import annotations

import copy
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Any, Final

from lintro.mcp.annotations import annotations_from_spec, tool_annotations_dict

__all__ = [
    "DEFAULT_TOOL_TIMEOUT_SECONDS",
    "McpToolRegistry",
    "McpToolSpec",
    "tool_annotations_dict",
]

# Default wall-clock budget for a single tool call. Toolkit handlers shell out
# to linters, and a hung subprocess would otherwise block that call forever with
# no recovery for the client short of dropping the connection.
DEFAULT_TOOL_TIMEOUT_SECONDS: Final[float] = 300.0


def _is_path_schema(*, schema: Any) -> bool:
    """Return whether a property schema can only hold path strings.

    Args:
        schema: The JSON Schema fragment describing a single property.

    Returns:
        True for ``{"type": "string"}`` or an array whose ``items`` schema is
        itself a string schema.
    """
    if not isinstance(schema, dict):
        return False
    schema_type = schema.get("type")
    if schema_type == "string":
        return True
    if schema_type == "array":
        return _is_path_schema(schema=schema.get("items"))
    return False


@dataclass(frozen=True)
class McpToolSpec:
    """Specification for a single MCP tool.

    Attributes:
        name: Unique tool name (e.g. ``lintro_ping``).
        description: Human-readable tool description.
        input_schema: JSON Schema object for tool arguments. Must be an object
            schema so the server can validate arguments before dispatch. The
            dict is deep-copied at construction, so a caller mutating the dict
            it passed in cannot retroactively weaken the validated schema.
        handler: Callable taking an arguments dict and returning a result. May
            be a coroutine function.
        read_only: Maps to MCP ``readOnlyHint``.
        destructive: Maps to MCP ``destructiveHint``.
        idempotent: Maps to MCP ``idempotentHint``.
        timeout_seconds: Wall-clock budget for a single call to ``handler``.
            Exceeding it aborts the call with an ``execution_error`` envelope
            carrying ``detail.reason == "timeout"``.
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
    timeout_seconds: float = DEFAULT_TOOL_TIMEOUT_SECONDS
    path_arguments: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """Validate the specification and freeze its schema.

        Raises:
            ValueError: If the name is empty, the timeout is not positive, the
                input schema is not a JSON Schema object, or a declared path
                argument is absent from the schema properties or not typed as a
                string (or array of strings).
        """
        if not self.name.strip():
            raise ValueError("MCP tool name must be a non-empty string")
        if self.timeout_seconds <= 0:
            raise ValueError(
                f"MCP tool {self.name!r} timeout_seconds must be positive",
            )
        if self.input_schema.get("type") != "object":
            raise ValueError(
                f"MCP tool {self.name!r} input_schema must be a JSON Schema "
                'object (\'"type": "object"\')',
            )
        object.__setattr__(self, "input_schema", copy.deepcopy(self.input_schema))

        properties = self.input_schema.get("properties") or {}
        for key in self.path_arguments:
            if key not in properties:
                raise ValueError(
                    f"MCP tool {self.name!r} declares path argument {key!r} "
                    "which is not present in input_schema properties",
                )
            if not _is_path_schema(schema=properties[key]):
                raise ValueError(
                    f"MCP tool {self.name!r} path argument {key!r} must be "
                    'typed as "string" or an array of "string" items',
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
        """Register multiple tools from a toolkit, atomically.

        Names are checked up front so a toolkit with one bad entry does not
        leave the registry half-populated.

        Args:
            specs: Tool specifications to register.

        Raises:
            ValueError: If any name is already registered or repeated within
                ``specs``. No tool from ``specs`` is registered in that case.
        """
        pending = tuple(specs)
        seen: set[str] = set()
        for spec in pending:
            if spec.name in self._tools or spec.name in seen:
                raise ValueError(f"MCP tool already registered: {spec.name}")
            seen.add(spec.name)
        for spec in pending:
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
