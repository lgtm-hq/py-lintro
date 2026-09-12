"""MCP server foundation for lintro.

The Python ``mcp`` SDK is an optional dependency (``lintro[mcp]``). Importing
this package pulls only stdlib-backed modules — enums, dataclasses, and path
guards — so the CLI and ``lintro doctor`` can reference it unconditionally.
Only starting the stdio server imports the SDK.
"""

from __future__ import annotations

import importlib.util
import sys
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
    "spec_has_server_subpackage",
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


# The submodule the stdio server imports first; resolving it also resolves
# ``mcp.server`` on the way, and both must be found for the SDK to count.
_REQUIRED_SDK_MODULE = "mcp.server.stdio"


def _find_submodule_spec(name: str, parent: ModuleSpec) -> ModuleSpec | None:
    """Locate a submodule spec without importing its parent package.

    ``importlib.util.find_spec("mcp.server")`` imports ``mcp`` first, which the
    probe must not do. Asking the ``sys.meta_path`` finders directly with the
    parent's search locations is the same lookup minus that import, and it
    works in a frozen binary too: Nuitka's finder answers from its module
    table, so no on-disk package directory is assumed.

    Args:
        name: Fully qualified submodule name.
        parent: The already located spec of the parent package.

    Returns:
        The submodule's spec, or ``None`` when no finder knows it.
    """
    for finder in sys.meta_path:
        find = getattr(finder, "find_spec", None)
        if find is None:
            continue
        try:
            found: ModuleSpec | None = find(name, parent.submodule_search_locations)
        except (ImportError, ValueError, AttributeError):
            continue
        if found is not None:
            return found
    return None


def spec_has_server_subpackage(spec: ModuleSpec) -> bool:
    """Return whether a top-level ``mcp`` spec is a package shipping the server.

    The stdio server imports ``mcp.server`` and ``mcp.server.stdio``, so a
    standalone ``mcp.py`` (no ``submodule_search_locations``) or a package
    without those submodules is not the SDK. Each submodule must also resolve
    outside lintro's own package, so a shadowing layout cannot satisfy it.

    Args:
        spec: The spec ``find_spec("mcp")`` located.

    Returns:
        True when the spec is a package and both required submodules resolve
        to specs outside lintro's package.
    """
    parent = spec
    parts = _REQUIRED_SDK_MODULE.split(".")
    # Walk the dotted path one level at a time: each finder lookup needs the
    # search locations of the immediate parent, not of ``mcp``.
    for depth in range(2, len(parts) + 1):
        if parent.submodule_search_locations is None:
            return False
        found = _find_submodule_spec(".".join(parts[:depth]), parent)
        if found is None or spec_is_lintro_subpackage(found):
            return False
        parent = found
    return True


def is_mcp_available() -> bool:
    """Return True when the optional ``mcp`` Python SDK is installed.

    The check uses :func:`importlib.util.find_spec` rather than an ``import``
    so a mere availability probe — which ``lintro doctor`` runs on every
    invocation — never executes the SDK, never leaves it in ``sys.modules``,
    and cannot be turned into a crash by a half-installed package.

    A spec that resolves inside lintro's own package is rejected: with
    ``lintro/`` on the search path ``find_spec("mcp")`` finds this subpackage,
    and reporting that as the SDK is how a release binary passed ``doctor``
    while ``lintro mcp`` died on ``import mcp.server`` (#2577). So is anything
    that is not a package carrying ``mcp.server`` and ``mcp.server.stdio``: a
    stray single-file ``mcp.py`` would pass ``require_mcp`` and fail on the
    server's first import. Those checks are spec-based, not filesystem-based,
    because inside a Nuitka onefile the bytecode SDK has no package directory
    to look in.

    Returns:
        True when a module spec for the SDK's ``mcp`` package can be located.
    """
    try:
        spec = importlib.util.find_spec("mcp")
    except (ImportError, ValueError):
        return False
    if spec is None or spec_is_lintro_subpackage(spec):
        return False
    return spec_has_server_subpackage(spec)


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
