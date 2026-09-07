"""Tool manager for Lintro.

This module provides the ToolManager class for managing tool registration and
execution ordering using the plugin registry system. Ordering is derived from
the claims each tool declares (:mod:`lintro.tools.core.scheduler`, #1742); the
scalar ``priority`` system and the unused ``conflicts_with`` machinery it
replaced are gone.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from lintro.plugins.discovery import discover_all_tools
from lintro.plugins.registry import ToolRegistry
from lintro.tools.core.scheduler import (
    build_order_report,
    derive_execution_order,
)

if TYPE_CHECKING:
    from lintro.plugins.base import BaseToolPlugin


@dataclass
class ToolManager:
    """Manager for tool registration and execution.

    This class is responsible for:
    - Tool discovery and registration via plugin registry
    - Tool execution order, derived from declared claims
    - Tool configuration management

    Execution order is not configurable: it is derived from what each tool
    declares it touches and what it does to it (``FIX`` -> ``FORMAT`` ->
    ``CHECK`` per pattern), so it is complete, verifiable and identical
    everywhere it is reported.
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

        The order is derived from the claims each tool declares: for every
        glob pattern, ``FIX`` runs before ``FORMAT`` runs before ``CHECK``,
        the per-pattern edges union into one DAG, each tool is invoked once,
        and ties break alphabetically. See
        :mod:`lintro.tools.core.scheduler`.

        Args:
            tool_names: List of tool names to order.
            ignore_conflicts: Accepted for call-site compatibility and
                ignored. Derived ordering demotes rather than drops, so no
                tool is ever removed from a run.

        Returns:
            List of tool names in derived execution order. Every requested
            tool appears exactly once.

        Raises:
            ValueError: If duplicate tools are found in tool_names.
        """
        del ignore_conflicts
        if not tool_names:
            return []

        normalized_names = [name.lower() for name in tool_names]

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

        # Resolving each tool proves it is registered before the scheduler
        # reads its claims, so an unknown name still fails loudly here.
        for name in normalized_names:
            self.get_tool(name)

        return derive_execution_order(normalized_names)

    def get_parallel_batches(self, tool_names: list[str]) -> list[list[str]]:
        """Group tools into batches that may run concurrently.

        Batches come from the same derived DAG that orders a sequential run:
        a tool sits in the batch after every tool that must precede it, so two
        tools never share a batch when one is required to observe the other's
        writes (ruff before black on ``*.py``). Tools with no derived relation
        share a batch, which is a proven independence rather than an unstated
        assumption.

        Args:
            tool_names: Tool names to batch, in derived execution order.

        Returns:
            Batches of tool names. Input order is preserved within a batch.
        """
        if not tool_names:
            return []

        predecessors: dict[str, set[str]] = {name: set() for name in tool_names}
        for edge in build_order_report(tool_names).edges:
            predecessors[edge.after].add(edge.before)

        level: dict[str, int] = {}
        remaining = list(tool_names)
        while remaining:
            ready = [name for name in remaining if predecessors[name] <= level.keys()]
            if not ready:
                # A derived cycle would stall the assignment; drop the whole
                # remainder into one final batch rather than looping forever.
                depth = max(level.values(), default=-1) + 1
                level.update(dict.fromkeys(remaining, depth))
                break
            for name in ready:
                level[name] = max(
                    (level[dep] + 1 for dep in predecessors[name]),
                    default=0,
                )
            remaining = [name for name in remaining if name not in level]

        return [
            [name for name in tool_names if level[name] == depth]
            for depth in sorted(set(level.values()))
        ]

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
