"""Tool implementations for Lintro.

This module provides the plugin-based tool system for Lintro.
Tools are automatically discovered and registered via the plugin registry.

``verify_pass`` is re-exported here rather than imported from its module path
directly: ``lintro.utils.tool_executor`` drives the mutate-then-verify pipeline
(#1743) and reaches it through this package, the one edge into ``lintro.tools``
the layering baseline already records for that module.
"""

from lintro.enums.tool_type import ToolType
from lintro.plugins import LintroPlugin, ToolDefinition, ToolRegistry
from lintro.tools.core import verify_pass
from lintro.tools.core.tool_manager import ToolManager

# Create global tool manager instance
tool_manager = ToolManager()

# Consolidated exports
__all__ = [
    "LintroPlugin",
    "ToolDefinition",
    "ToolRegistry",
    "ToolType",
    "ToolManager",
    "tool_manager",
    "verify_pass",
]
