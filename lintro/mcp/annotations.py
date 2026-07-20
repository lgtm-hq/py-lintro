"""Map lintro tool capability flags to MCP tool annotation hints."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from lintro.mcp.registry import McpToolSpec


def tool_annotations_dict(
    *,
    read_only: bool,
    destructive: bool,
    idempotent: bool,
) -> dict[str, bool]:
    """Build MCP tool annotation hints from capability flags.

    Args:
        read_only: Tool only reads state (``readOnlyHint``).
        destructive: Tool may destroy or irreversibly change state
            (``destructiveHint``).
        idempotent: Repeated identical calls are safe (``idempotentHint``).

    Returns:
        Dict with ``readOnlyHint``, ``destructiveHint``, and ``idempotentHint``.
    """
    return {
        "readOnlyHint": read_only,
        "destructiveHint": destructive,
        "idempotentHint": idempotent,
    }


def annotations_from_spec(spec: McpToolSpec) -> dict[str, bool]:
    """Map a :class:`McpToolSpec` to MCP annotation hints.

    Args:
        spec: Registered tool specification.

    Returns:
        Dict with MCP ``*Hint`` keys.
    """
    return tool_annotations_dict(
        read_only=spec.read_only,
        destructive=spec.destructive,
        idempotent=spec.idempotent,
    )
