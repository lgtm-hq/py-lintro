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

from lintro.plugins.file_processor import AggregatedResult, FileProcessingResult
from lintro.plugins.protocol import (
    LINTRO_PLUGIN_API_VERSION,
    LintroPlugin,
    ToolDefinition,
    is_compatible_api_version,
)
from lintro.plugins.registry import ToolRegistry, register_tool

# BaseToolPlugin is imported lazily to avoid circular imports
# Use: from lintro.plugins.base import BaseToolPlugin

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
