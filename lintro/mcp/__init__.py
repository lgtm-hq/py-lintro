"""MCP server foundation for lintro.

The Python ``mcp`` SDK is an optional dependency (``lintro[mcp]``). Importing
this package does not require the SDK; only starting the stdio server does.
"""

from __future__ import annotations

from lintro.mcp.annotations import tool_annotations_dict
from lintro.mcp.errors import (
    McpError,
    McpErrorCode,
    McpErrorEnvelope,
    ensure_within_workspace,
)
from lintro.mcp.registry import McpToolRegistry, McpToolSpec

__all__ = [
    "McpError",
    "McpErrorCode",
    "McpErrorEnvelope",
    "McpToolRegistry",
    "McpToolSpec",
    "ensure_within_workspace",
    "is_mcp_available",
    "require_mcp",
    "tool_annotations_dict",
]


def is_mcp_available() -> bool:
    """Return True when the optional ``mcp`` Python SDK is importable.

    Returns:
        True when ``import mcp`` succeeds.
    """
    try:
        import mcp  # noqa: F401
    except ImportError:
        return False
    return True


def require_mcp() -> None:
    """Ensure the optional MCP SDK is installed.

    Raises:
        click.UsageError: When the ``mcp`` package is not importable.
    """
    import click

    if not is_mcp_available():
        raise click.UsageError(
            "MCP server requires lintro[mcp]. "
            "Install with: uv pip install 'lintro[mcp]'",
        )
