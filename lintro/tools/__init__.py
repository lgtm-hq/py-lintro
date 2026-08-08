"""Tool implementations for Lintro.

This module provides the plugin-based tool system for Lintro.
Tools are automatically discovered and registered via the plugin registry.
Exports resolve lazily so importing sibling tool helpers does not pull the
full plugin/base/config chain at cold start.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from lintro.enums.tool_type import ToolType as ToolType
    from lintro.plugins import LintroPlugin as LintroPlugin
    from lintro.plugins import ToolDefinition as ToolDefinition
    from lintro.plugins import ToolRegistry as ToolRegistry
    from lintro.tools.core.tool_manager import ToolManager as ToolManager
    from lintro.tools.core.tool_manager import tool_manager as tool_manager

_LAZY_EXPORTS: dict[str, tuple[str, str]] = {
    "LintroPlugin": ("lintro.plugins", "LintroPlugin"),
    "ToolDefinition": ("lintro.plugins", "ToolDefinition"),
    "ToolRegistry": ("lintro.plugins", "ToolRegistry"),
    "ToolType": ("lintro.enums.tool_type", "ToolType"),
    "ToolManager": ("lintro.tools.core.tool_manager", "ToolManager"),
    "tool_manager": ("lintro.tools.core.tool_manager", "tool_manager"),
}

__all__ = [
    "LintroPlugin",
    "ToolDefinition",
    "ToolRegistry",
    "ToolType",
    "ToolManager",
    "tool_manager",  # noqa: F822 - resolved via module __getattr__
]


def __getattr__(name: str) -> Any:
    """Resolve tool package exports on first access.

    Args:
        name: Attribute name being accessed.

    Returns:
        The lazily imported attribute.

    Raises:
        AttributeError: If ``name`` is not a public export.
    """
    if name not in _LAZY_EXPORTS:
        msg = f"module {__name__!r} has no attribute {name!r}"
        raise AttributeError(msg)
    module_name, attr_name = _LAZY_EXPORTS[name]
    module = __import__(module_name, fromlist=[attr_name])
    value = getattr(module, attr_name)
    globals()[name] = value
    return value
