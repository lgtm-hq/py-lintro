"""Tool manager for Lintro.

This module provides the ToolManager class for managing tool registration,
conflict resolution, and execution ordering using the plugin registry system.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from loguru import logger

from lintro.plugins.discovery import discover_all_tools
from lintro.plugins.registry import ToolRegistry
from lintro.utils.unified_config import get_ordered_tools

if TYPE_CHECKING:
    from lintro.plugins.base import BaseToolPlugin


@dataclass
class ToolManager:
    """Manager for tool registration and execution.

    This class is responsible for:
    - Tool discovery and registration via plugin registry
    - Tool conflict resolution
    - Tool execution order (priority-based, alphabetical, or custom)
    - Tool configuration management

    Tool ordering is controlled by [tool.lintro].tool_order in pyproject.toml:
    - "priority" (default): Formatters run before linters based on priority values
    - "alphabetical": Tools run in alphabetical order by name
    - "custom": Tools run in order specified by [tool.lintro].tool_order_custom
    """

    _initialized: bool = field(default=False, init=False)

    def _ensure_initialized(self) -> None:
        """Ensure tools are discovered and registered."""
        if not self._initialized:
            discover_all_tools()
            self._initialized = True

    def get_tool(self, name: str) -> BaseToolPlugin:
        """Get a tool instance by name.

        Args:
            name: The name of the tool (case-insensitive).

        Returns:
            The tool/plugin instance.
        """
        self._ensure_initialized()
        return ToolRegistry.get(name)

    def get_tool_execution_order(
        self,
        tool_names: list[str],
        ignore_conflicts: bool = False,
    ) -> list[str]:
        """Get the order in which tools should be executed.

        Tool ordering is controlled by [tool.lintro].tool_order in pyproject.toml:
        - "priority" (default): Formatters run before linters based on priority
        - "alphabetical": Tools run in alphabetical order by name
        - "custom": Tools run in order specified by [tool.lintro].tool_order_custom

        This method also handles:
        - Tool conflicts (unless ignore_conflicts is True)

        Args:
            tool_names: List of tool names to order.
            ignore_conflicts: If True, skip conflict checking.

        Returns:
            List of tool names in execution order based on configured strategy.

        Raises:
            ValueError: If duplicate tools are found in tool_names.
        """
        if not tool_names:
            return []

        # Normalize names to lowercase
        normalized_names = [name.lower() for name in tool_names]

        # Get tool instances
        tools: dict[str, BaseToolPlugin] = {
            name: self.get_tool(name) for name in normalized_names
        }

        # Validate for duplicate tools
        seen_names: set[str] = set()
        duplicates: list[str] = []
        for name in normalized_names:
            if name in seen_names:
                duplicates.append(name)
            else:
                seen_names.add(name)
        if duplicates:
            raise ValueError(
                f"Duplicate tools found in tool_names: {', '.join(duplicates)}",
            )

        # Get ordered tool names from unified config
        ordered_names = get_ordered_tools(normalized_names)

        # Validate that all requested tools are preserved
        original_set = set(normalized_names)
        sorted_set = set(ordered_names)
        missing_tools = original_set - sorted_set
        if missing_tools:
            # Append missing tools in their original order
            missing_list = [n for n in normalized_names if n in missing_tools]
            ordered_names.extend(missing_list)
            logger.warning(
                f"Some tools were not found in ordered list and appended: "
                f"{missing_list}",
            )

        if ignore_conflicts:
            return ordered_names

        # Build conflict graph
        conflict_graph: dict[str, set[str]] = {name: set() for name in normalized_names}
        for tool_name in normalized_names:
            tool_instance = tools[tool_name]
            for conflict in tool_instance.definition.conflicts_with:
                conflict_lower = conflict.lower()
                # Only add to conflict graph if conflict is in our tool list
                if conflict_lower in normalized_names:
                    conflict_graph[tool_name].add(conflict_lower)
                    conflict_graph[conflict_lower].add(tool_name)

        # Resolve conflicts by keeping the first tool in ordered sequence
        result: list[str] = []
        for tool_name in ordered_names:
            # Check if this tool conflicts with any already selected tools
            conflicts = conflict_graph[tool_name] & set(result)
            if not conflicts:
                result.append(tool_name)

        return result

    def set_tool_options(
        self,
        name: str,
        **options: Any,
    ) -> None:
        """Set options for a tool.

        Args:
            name: The name of the tool.
            **options: The options to set.
        """
        tool = self.get_tool(name)
        tool.set_options(**options)

    def get_all_tools(self) -> dict[str, BaseToolPlugin]:
        """Get all registered tools.

        Returns:
            Dictionary mapping tool names to plugin instances.
        """
        self._ensure_initialized()
        return ToolRegistry.get_all()

    def get_check_tools(self) -> dict[str, BaseToolPlugin]:
        """Get all tools that can check files.

        Returns:
            Dictionary mapping tool names to plugin instances.
        """
        self._ensure_initialized()
        return ToolRegistry.get_check_tools()

    def get_fix_tools(self) -> dict[str, BaseToolPlugin]:
        """Get all tools that can fix files.

        Returns:
            Dictionary mapping tool names to plugin instances.
        """
        self._ensure_initialized()
        return ToolRegistry.get_fix_tools()

    def get_tool_names(self) -> list[str]:
        """Get all registered tool names.

        Returns:
            Sorted list of tool names.
        """
        self._ensure_initialized()
        return ToolRegistry.get_names()

    def is_tool_registered(self, name: str) -> bool:
        """Check if a tool is registered.

        Args:
            name: Tool name (case-insensitive).

        Returns:
            True if the tool is registered.
        """
        self._ensure_initialized()
        return ToolRegistry.is_registered(name)


# Process-wide singleton used by CLI execution paths.
tool_manager = ToolManager()
