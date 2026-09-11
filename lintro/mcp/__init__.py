"""MCP server foundation for lintro.

The Python ``mcp`` SDK is an optional dependency (``lintro[mcp]``). Importing
this package pulls only stdlib-backed modules — enums, dataclasses, and path
guards — so the CLI and ``lintro doctor`` can reference it unconditionally.
Only starting the stdio server imports the SDK.
"""

from __future__ import annotations

import importlib.util
from importlib.machinery import ModuleSpec
from pathlib import Path

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
    "spec_is_lintro_subpackage",
    "tool_annotations_dict",
]


# lintro's own package directory. A top-level ``mcp`` spec that resolves in
# here is this very subpackage, found because ``lintro/`` sits on the module
# search path (a frozen binary built with ``lintro/`` as its import root did
# exactly that, #2577), not the SDK.
_LINTRO_PACKAGE_DIR = Path(__file__).resolve().parent.parent


def _spec_locations(spec: ModuleSpec) -> list[str]:
    """Return every filesystem location a module spec points at.

    Args:
        spec: The located module spec.

    Returns:
        The origin file (when it is a path) plus any package search
        locations; empty for built-in and namespace-less specs.
    """
    locations: list[str] = []
    origin = getattr(spec, "origin", None)
    if isinstance(origin, str) and origin not in ("built-in", "frozen"):
        locations.append(origin)
    search = getattr(spec, "submodule_search_locations", None) or ()
    locations.extend(str(location) for location in search)
    return locations


def spec_is_lintro_subpackage(spec: ModuleSpec) -> bool:
    """Return whether a top-level ``mcp`` spec is lintro's own ``lintro.mcp``.

    Args:
        spec: The spec ``find_spec("mcp")`` located.

    Returns:
        True when the spec's origin or package directory lies inside lintro's
        package directory.
    """
    for location in _spec_locations(spec):
        try:
            resolved = Path(location).resolve()
        except OSError:
            continue
        if resolved.is_relative_to(_LINTRO_PACKAGE_DIR):
            return True
    return False


def is_mcp_available() -> bool:
    """Return True when the optional ``mcp`` Python SDK is installed.

    The check uses :func:`importlib.util.find_spec` rather than an ``import``
    so a mere availability probe — which ``lintro doctor`` runs on every
    invocation — never executes the SDK, never leaves it in ``sys.modules``,
    and cannot be turned into a crash by a half-installed package.

    A spec that resolves inside lintro's own package is rejected: with
    ``lintro/`` on the search path ``find_spec("mcp")`` finds this subpackage,
    and reporting that as the SDK is how a release binary passed ``doctor``
    while ``lintro mcp`` died on ``import mcp.server`` (#2577).

    Returns:
        True when a module spec for the SDK's ``mcp`` package can be located.
    """
    try:
        spec = importlib.util.find_spec("mcp")
    except (ImportError, ValueError):
        return False
    if spec is None:
        return False
    return not spec_is_lintro_subpackage(spec)


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
