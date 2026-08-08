"""Lintro plugin system.

This package provides the plugin architecture for Lintro, enabling both
built-in tools and external plugins to be registered and discovered.

Core Components:
    - LintroPlugin: Protocol defining the tool interface
    - ToolDefinition: Dataclass for tool metadata
    - ToolRegistry: Central registry for tool registration
    - BaseToolPlugin: Base class for implementing tools
    - register_tool: Decorator for registering tools

Example:
    Creating a custom tool plugin:

    >>> from lintro.plugins import ToolDefinition, register_tool
    >>> from lintro.plugins.base import BaseToolPlugin
    >>> from lintro.enums.tool_type import ToolType
    >>> from lintro.models.core.tool_result import ToolResult
    >>>
    >>> @register_tool
    ... class MyPlugin(BaseToolPlugin):
    ...     @property
    ...     def definition(self) -> ToolDefinition:
    ...         return ToolDefinition(
    ...             name="my-tool",
    ...             description="My custom linting tool",
    ...             tool_type=ToolType.LINTER,
    ...             file_patterns=["*.py"],
    ...         )
    ...
    ...     def check(self, paths: list[str], options: dict[str, object]) -> ToolResult:
    ...         # Implementation here
    ...         return ToolResult(name="my-tool", success=True, issues_count=0)

    Using the registry:

    >>> from lintro.plugins import ToolRegistry
    >>> from lintro.plugins.discovery import discover_all_tools
    >>>
    >>> discover_all_tools()  # Load all available tools
    >>> tool = ToolRegistry.get("my-tool")
    >>> result = tool.check(["."], {})
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from lintro.plugins.file_processor import AggregatedResult as AggregatedResult
    from lintro.plugins.file_processor import (
        FileProcessingResult as FileProcessingResult,
    )
    from lintro.plugins.protocol import (
        LINTRO_PLUGIN_API_VERSION as LINTRO_PLUGIN_API_VERSION,
    )
    from lintro.plugins.protocol import LintroPlugin as LintroPlugin
    from lintro.plugins.protocol import ToolDefinition as ToolDefinition
    from lintro.plugins.protocol import (
        is_compatible_api_version as is_compatible_api_version,
    )
    from lintro.plugins.registry import ToolRegistry as ToolRegistry
    from lintro.plugins.registry import register_tool as register_tool

# BaseToolPlugin is imported lazily to avoid circular imports
# Use: from lintro.plugins.base import BaseToolPlugin

_LAZY_EXPORTS: dict[str, tuple[str, str]] = {
    "LINTRO_PLUGIN_API_VERSION": (
        "lintro.plugins.protocol",
        "LINTRO_PLUGIN_API_VERSION",
    ),
    "AggregatedResult": ("lintro.plugins.file_processor", "AggregatedResult"),
    "FileProcessingResult": ("lintro.plugins.file_processor", "FileProcessingResult"),
    "LintroPlugin": ("lintro.plugins.protocol", "LintroPlugin"),
    "ToolDefinition": ("lintro.plugins.protocol", "ToolDefinition"),
    "ToolRegistry": ("lintro.plugins.registry", "ToolRegistry"),
    "is_compatible_api_version": (
        "lintro.plugins.protocol",
        "is_compatible_api_version",
    ),
    "register_tool": ("lintro.plugins.registry", "register_tool"),
}

__all__ = [
    "LINTRO_PLUGIN_API_VERSION",
    "AggregatedResult",
    "FileProcessingResult",
    "LintroPlugin",
    "ToolDefinition",
    "ToolRegistry",
    "is_compatible_api_version",
    "register_tool",
]


def __getattr__(name: str) -> Any:
    """Resolve plugin package exports on first access.

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
