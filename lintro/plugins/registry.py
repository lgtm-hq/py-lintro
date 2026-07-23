"""Tool registry for discovering and managing Lintro plugins.

This module provides a central registry for all Lintro tools, supporting
both built-in tools and external plugins discovered via entry points. It
owns *live plugin instances* (registered ``BaseToolPlugin`` subclasses) and
handles registration, lazy instantiation, and lookup by name.

This is distinct from :class:`lintro.tools.core.tool_registry.ManifestRegistry`
(formerly also named ``ToolRegistry``), which owns static tool *metadata*
(versions, install commands, language mappings, profiles) parsed from
``manifest.json`` rather than live plugin instances.

Example:
    >>> from lintro.plugins.registry import ToolRegistry, register_tool
    >>> from lintro.plugins.base import BaseToolPlugin
    >>>
    >>> @register_tool
    ... class MyPlugin(BaseToolPlugin):
    ...     # Plugin implementation
    ...     pass
    >>>
    >>> tool = ToolRegistry.get("my-tool")
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

from loguru import logger

from lintro.plugins.base import BaseToolPlugin

if TYPE_CHECKING:
    from lintro.plugins.protocol import ToolDefinition


class ToolRegistry:
    """Central registry for all Lintro tools.

    This class maintains a registry of all available tools, both built-in
    and external plugins. It provides methods for registering, retrieving,
    and listing tools.

    The registry is thread-safe and uses lazy instantiation for tool instances.

    Not to be confused with
    :class:`lintro.tools.core.tool_registry.ManifestRegistry`, which manages
    manifest-derived tool metadata rather than live plugin instances.
    """

    #: Origin label used for tools shipped inside lintro itself.
    BUILTIN_ORIGIN: str = "builtin"

    _tools: dict[str, type[BaseToolPlugin]] = {}
    _instances: dict[str, BaseToolPlugin] = {}
    _origins: dict[str, str] = {}
    _lock: threading.RLock = threading.RLock()  # Reentrant lock for nested calls

    @classmethod
    def register(
        cls,
        plugin_class: type[BaseToolPlugin],
        *,
        origin: str = BUILTIN_ORIGIN,
        instance: BaseToolPlugin | None = None,
    ) -> type[BaseToolPlugin]:
        """Register a tool class.

        Can be used as a decorator or called directly.

        Args:
            plugin_class: The tool class to register.
            origin: Where the tool came from — ``"builtin"`` for tools shipped
                with lintro (the default, so decorator usage stays unchanged) or
                the distribution/package name for third-party plugins loaded via
                entry points. Surfaced by ``lintro list-tools``.
            instance: Pre-built plugin instance to adopt. When provided it is
                reused instead of instantiating ``plugin_class`` again, which
                lets discovery probe a plugin's name once and register the same
                instance without paying for a second construction.

        Returns:
            The registered tool class (allows use as decorator).

        Example:
            >>> @ToolRegistry.register
            ... class MyPlugin(BaseToolPlugin):
            ...     pass
        """
        with cls._lock:
            # Create a temporary instance to get the definition (unless the
            # caller already built one).
            if instance is None:
                instance = plugin_class()
            name = instance.definition.name.lower()

            if name in cls._tools:
                existing = cls._tools[name]
                logger.warning(
                    f"Tool '{name}' already registered by {existing.__module__}."
                    f"{existing.__name__}, overwriting with {plugin_class.__module__}."
                    f"{plugin_class.__name__}",
                )

            cls._tools[name] = plugin_class
            # Store the instance we created for get() calls
            cls._instances[name] = instance
            cls._origins[name] = origin
            logger.debug(f"Registered tool: {name} (origin={origin})")

        return plugin_class

    @classmethod
    def _ensure_discovered(cls) -> None:
        """Ensure tools have been discovered.

        This is called automatically when accessing tools to support
        lazy discovery when the package is imported.
        """
        if not cls._tools:
            # Auto-discover tools if registry is empty
            from lintro.plugins.discovery import discover_all_tools

            discover_all_tools()

    @classmethod
    def get(cls, name: str) -> BaseToolPlugin:
        """Get a tool instance by name.

        Args:
            name: Tool name (case-insensitive).

        Returns:
            The tool instance.

        Raises:
            ValueError: If the tool is not registered.

        Example:
            >>> tool = ToolRegistry.get("hadolint")
            >>> result = tool.check(["."], {})
        """
        name_lower = name.lower()

        with cls._lock:
            # Auto-discover tools if not yet done
            cls._ensure_discovered()

            if name_lower not in cls._instances:
                if name_lower not in cls._tools:
                    available = ", ".join(sorted(cls._tools.keys()))
                    raise ValueError(
                        f"Unknown tool: {name!r}. "
                        f"Available tools: {available or 'none'}",
                    )
                cls._instances[name_lower] = cls._tools[name_lower]()

            return cls._instances[name_lower]

    @classmethod
    def get_all(cls) -> dict[str, BaseToolPlugin]:
        """Get all registered tool instances.

        Returns:
            Dictionary mapping tool names to tool instances.

        Example:
            >>> all_tools = ToolRegistry.get_all()
            >>> for name, tool in all_tools.items():
            ...     print(f"{name}: {tool.definition.description}")
        """
        with cls._lock:
            cls._ensure_discovered()
            return {name: cls.get(name) for name in cls._tools}

    @classmethod
    def get_definitions(cls) -> dict[str, ToolDefinition]:
        """Get all tool definitions.

        Returns:
            Dictionary mapping tool names to their definitions.

        Example:
            >>> defs = ToolRegistry.get_definitions()
            >>> for name, defn in defs.items():
            ...     print(f"{name}: can_fix={defn.can_fix}")
        """
        with cls._lock:
            cls._ensure_discovered()
            return {name: cls.get(name).definition for name in cls._tools}

    @classmethod
    def get_names(cls) -> list[str]:
        """Get all registered tool names.

        Returns:
            Sorted list of tool names.
        """
        with cls._lock:
            cls._ensure_discovered()
            return sorted(cls._tools.keys())

    @classmethod
    def get_origin(cls, name: str) -> str:
        """Return the origin label for a registered tool.

        Args:
            name: Tool name (case-insensitive).

        Returns:
            ``"builtin"`` for tools shipped with lintro, or the distribution
            name for a third-party plugin. Returns ``"unknown"`` if the tool
            has no recorded origin (e.g. registered by legacy code paths).
        """
        with cls._lock:
            cls._ensure_discovered()
            return cls._origins.get(name.lower(), "unknown")

    @classmethod
    def is_registered(cls, name: str) -> bool:
        """Check if a tool is registered.

        Args:
            name: Tool name (case-insensitive).

        Returns:
            True if the tool is registered, False otherwise.
        """
        with cls._lock:
            cls._ensure_discovered()
            return name.lower() in cls._tools

    @classmethod
    def clear(cls) -> None:
        """Clear all registered tools.

        This is primarily useful for testing.
        """
        with cls._lock:
            cls._tools.clear()
            cls._instances.clear()
            cls._origins.clear()
            logger.debug("Cleared tool registry")

    @classmethod
    def get_check_tools(cls) -> dict[str, BaseToolPlugin]:
        """Get all tools that support checking (all tools).

        Returns:
            Dictionary mapping tool names to tool instances.
        """
        return cls.get_all()

    @classmethod
    def get_fix_tools(cls) -> dict[str, BaseToolPlugin]:
        """Get all tools that support fixing.

        Returns:
            Dictionary mapping tool names to tool instances for fix-capable tools.
        """
        all_tools = cls.get_all()
        return {
            name: tool for name, tool in all_tools.items() if tool.definition.can_fix
        }


def register_tool(cls: type[BaseToolPlugin]) -> type[BaseToolPlugin]:
    """Decorator to register a tool class.

    This is a convenience function that wraps ToolRegistry.register().

    Args:
        cls: The tool class to register.

    Returns:
        The registered tool class.

    Example:
        >>> from lintro.plugins.registry import register_tool
        >>> from lintro.plugins.base import BaseToolPlugin
        >>>
        >>> @register_tool
        ... class HadolintPlugin(BaseToolPlugin):
        ...     @property
        ...     def definition(self) -> ToolDefinition:
        ...         return ToolDefinition(name="hadolint", description="...")
    """
    return ToolRegistry.register(cls)
