"""MCP toolkits: the tool groups registered on the lintro MCP server.

Each toolkit module exposes a ``build_*_toolkit(*, workspace=...)`` factory
returning :class:`~lintro.mcp.registry.McpToolSpec` values, which
:func:`lintro.mcp.server.build_default_registry` feeds to
``McpToolRegistry.register_toolkit`` so a toolkit lands atomically or not at
all.
"""

from __future__ import annotations

from lintro.mcp.toolkits.lint import build_lint_toolkit

__all__ = ["build_lint_toolkit"]
