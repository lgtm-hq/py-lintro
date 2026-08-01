"""Argument validation for MCP tool dispatch.

Two guards run before any handler executes:

1. JSON Schema validation of the raw arguments against the tool's declared
   ``input_schema``.
2. Workspace-boundary resolution of every property named in the tool's
   ``path_arguments``.

Both are applied centrally by :mod:`lintro.mcp.server` rather than by each
toolkit, so a handler can never be reached with unvalidated input or with a
path pointing outside the workspace root.

``jsonschema`` is a hard dependency of the ``mcp`` SDK, so it is always present
whenever the server can actually run; it is imported lazily all the same to
keep this module importable without the optional extra.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from lintro.mcp.enums.mcp_error_code import McpErrorCode
from lintro.mcp.errors import McpError, ensure_within_workspace

if TYPE_CHECKING:
    from lintro.mcp.registry import McpToolSpec

__all__ = ["resolve_path_arguments", "validate_arguments"]


def validate_arguments(
    *,
    spec: McpToolSpec,
    arguments: dict[str, Any],
) -> None:
    """Validate raw tool arguments against the tool's JSON Schema.

    Args:
        spec: Tool specification providing ``input_schema``.
        arguments: Raw arguments received from the MCP client.

    Raises:
        McpError: With :attr:`McpErrorCode.INVALID_INPUT` when the arguments
            do not satisfy the schema.
    """
    import jsonschema

    try:
        jsonschema.validate(instance=arguments, schema=spec.input_schema)
    except jsonschema.ValidationError as exc:
        raise McpError(
            code=McpErrorCode.INVALID_INPUT,
            message=f"Invalid arguments for {spec.name}: {exc.message}",
            detail={
                "tool": spec.name,
                "field": "/".join(str(part) for part in exc.absolute_path) or None,
            },
        ) from exc


def resolve_path_arguments(
    *,
    spec: McpToolSpec,
    arguments: dict[str, Any],
    workspace: Path,
) -> dict[str, Any]:
    """Resolve declared path arguments and enforce workspace containment.

    Args:
        spec: Tool specification declaring ``path_arguments``.
        arguments: Schema-validated arguments.
        workspace: Workspace root the server is anchored to.

    Returns:
        A copy of ``arguments`` where every declared path property has been
        replaced by its resolved absolute path (or list of resolved paths).

    Raises:
        McpError: :attr:`McpErrorCode.WORKSPACE_VIOLATION` when a path escapes
            the workspace, or :attr:`McpErrorCode.INVALID_INPUT` when a path
            property is neither a string nor a list of strings.
    """
    if not spec.path_arguments:
        return dict(arguments)

    resolved_arguments = dict(arguments)
    for key in spec.path_arguments:
        if key not in resolved_arguments:
            continue
        value = resolved_arguments[key]
        if isinstance(value, str):
            resolved_arguments[key] = str(
                ensure_within_workspace(path=value, workspace=workspace),
            )
            continue
        if isinstance(value, list) and all(isinstance(item, str) for item in value):
            resolved_arguments[key] = [
                str(ensure_within_workspace(path=item, workspace=workspace))
                for item in value
            ]
            continue
        raise McpError(
            code=McpErrorCode.INVALID_INPUT,
            message=(
                f"Path argument {key!r} for {spec.name} must be a string or a "
                "list of strings"
            ),
            detail={"tool": spec.name, "field": key, "type": type(value).__name__},
        )
    return resolved_arguments
