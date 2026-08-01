"""MCP server foundation for lintro.

The Python ``mcp`` SDK is an optional dependency (``lintro[mcp]``). Importing
this package pulls only stdlib-backed modules — enums, dataclasses, and path
guards — so the CLI and ``lintro doctor`` can reference it unconditionally.
Only starting the stdio server imports the SDK.
"""

from __future__ import annotations

import importlib.util

from lintro.mcp.annotations import annotations_from_spec, tool_annotations_dict
from lintro.mcp.enums.mcp_error_code import McpErrorCode
from lintro.mcp.errors import (
    McpError,
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
    "annotations_from_spec",
    "ensure_within_workspace",
    "is_mcp_available",
    "require_mcp",
    "tool_annotations_dict",
]


def is_mcp_available() -> bool:
    """Return True when the optional ``mcp`` Python SDK is installed.

    The check uses :func:`importlib.util.find_spec` rather than an ``import``
    so a mere availability probe — which ``lintro doctor`` runs on every
    invocation — never executes the SDK, never leaves it in ``sys.modules``,
    and cannot be turned into a crash by a half-installed package.

    Returns:
        True when a module spec for ``mcp`` can be located.
    """
    try:
        return importlib.util.find_spec("mcp") is not None
    except (ImportError, ValueError):
        return False


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
