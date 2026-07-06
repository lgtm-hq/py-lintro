"""Tool discovery for builtin and external plugins.

This module handles discovering and loading Lintro tools from:
1. Built-in tool definitions (lintro/tools/definitions/)
2. External (third-party) plugins via Python entry points (``lintro.tools``)

Third-party packages register a tool plugin by advertising an entry point in
the ``lintro.tools`` group::

    [project.entry-points."lintro.tools"]
    my-tool = "my_package.plugin:MyToolPlugin"

At startup the registry discovers every such entry point, validates it against
the public plugin contract (see :mod:`lintro.plugins.protocol`), checks API
version compatibility, and registers well-formed plugins alongside builtins.
A malformed plugin is logged and skipped — it never crashes lintro or blocks
discovery of the remaining plugins.

Example:
    >>> from lintro.plugins.discovery import discover_all_tools
    >>> discover_all_tools()  # Loads all available tools
"""

from __future__ import annotations

import importlib
import importlib.metadata
from pathlib import Path
from typing import TYPE_CHECKING, cast

from loguru import logger

from lintro.plugins.base import BaseToolPlugin
from lintro.plugins.protocol import (
    LINTRO_PLUGIN_API_VERSION,
    is_compatible_api_version,
)
from lintro.plugins.registry import ToolRegistry

if TYPE_CHECKING:
    from importlib.metadata import EntryPoint

# Path to builtin tool definitions
BUILTIN_DEFINITIONS_PATH = Path(__file__).parent.parent / "tools" / "definitions"

# Entry point group third-party packages use to register tool plugins.
ENTRY_POINT_GROUP = "lintro.tools"

# Attributes a plugin class must expose to satisfy the LintroPlugin contract.
_REQUIRED_PLUGIN_ATTRS = ("definition", "check", "fix", "set_options")

# Track whether discovery has been performed
_discovered: bool = False


def discover_builtin_tools() -> int:
    """Load all builtin tool definitions.

    This function imports all Python modules in the tools/definitions/
    directory, which triggers the @register_tool decorators.

    Returns:
        Number of tool modules loaded.

    Note:
        Each tool definition file should use the @register_tool decorator
        to register itself with the ToolRegistry.
    """
    loaded_count = 0

    if not BUILTIN_DEFINITIONS_PATH.exists():
        logger.warning(
            f"Builtin definitions path not found: {BUILTIN_DEFINITIONS_PATH}",
        )
        return loaded_count

    for py_file in BUILTIN_DEFINITIONS_PATH.glob("*.py"):
        if py_file.name.startswith("_"):
            continue

        module_name = f"lintro.tools.definitions.{py_file.stem}"
        try:
            # Safe: module_name from internal directory files, not user input
            importlib.import_module(module_name)  # nosemgrep: non-literal-import
            logger.debug(f"Loaded builtin tool: {py_file.stem}")
            loaded_count += 1
        except ImportError as e:
            logger.warning(f"Failed to import {module_name}: {e}")
        except (AttributeError, TypeError, ValueError) as e:
            logger.error(f"Error loading {module_name}: {type(e).__name__}: {e}")

    logger.debug(f"Loaded {loaded_count} builtin tool definitions")
    return loaded_count


def _entry_point_origin(ep: EntryPoint) -> str:
    """Derive a human-readable origin label for an entry point.

    Args:
        ep: The entry point being loaded.

    Returns:
        The distribution (package) name that shipped the plugin when known,
        falling back to the entry-point's module and finally its name. This is
        what ``lintro list-tools`` shows so users can tell where a third-party
        tool came from.
    """
    dist = getattr(ep, "dist", None)
    dist_name = getattr(dist, "name", None) if dist is not None else None
    if dist_name:
        return str(dist_name)

    value = getattr(ep, "value", "") or ""
    module = value.split(":", 1)[0].strip()
    if module:
        return module

    return str(getattr(ep, "name", "external"))


def _validate_plugin_class(ep: EntryPoint, plugin_class: object) -> bool:
    """Validate a loaded entry-point object against the plugin contract.

    Performs the checks that do not require instantiating the plugin: that the
    object is a class, exposes the required ``LintroPlugin`` surface, and
    declares a compatible plugin-API version.

    Args:
        ep: The entry point the object was loaded from (used for diagnostics).
        plugin_class: The object returned by ``EntryPoint.load()``.

    Returns:
        True if the object is a well-formed, API-compatible plugin class.
    """
    if not isinstance(plugin_class, type):
        logger.warning(
            f"Entry point {ep.name!r} does not point to a class, skipping",
        )
        return False

    # Protocols with properties can't be used with issubclass reliably, so
    # check for the required attributes that make up the LintroPlugin contract.
    if not all(hasattr(plugin_class, attr) for attr in _REQUIRED_PLUGIN_ATTRS):
        logger.warning(
            f"Entry point {ep.name!r} class {plugin_class.__name__!r} does not "
            "implement the LintroPlugin contract (missing "
            f"{[a for a in _REQUIRED_PLUGIN_ATTRS if not hasattr(plugin_class, a)]}"
            "), skipping",
        )
        return False

    declared_version = getattr(plugin_class, "LINTRO_PLUGIN_API_VERSION", None)
    if not is_compatible_api_version(declared_version):
        logger.warning(
            f"Plugin {ep.name!r} targets plugin API version {declared_version!r}, "
            f"which is incompatible with this lintro "
            f"(API version {LINTRO_PLUGIN_API_VERSION}); skipping",
        )
        return False
    if declared_version is None:
        logger.debug(
            f"Plugin {ep.name!r} does not declare LINTRO_PLUGIN_API_VERSION; "
            "assuming compatibility. Declaring it is recommended.",
        )

    return True


def discover_external_plugins() -> int:
    """Load third-party plugins advertised via the ``lintro.tools`` group.

    External packages register a plugin by defining an entry point in their
    ``pyproject.toml``::

        [project.entry-points."lintro.tools"]
        my-tool = "my_package.plugin:MyToolPlugin"

    Each entry point is loaded, validated against the public plugin contract,
    checked for API compatibility, and registered. Failure is fully isolated:
    a plugin that fails to import, is malformed, declares an incompatible API
    version, collides with a builtin name, or raises on instantiation is logged
    and skipped without affecting the other plugins or crashing lintro.

    Returns:
        Number of external plugins successfully loaded.
    """
    loaded_count = 0

    try:
        entry_points = importlib.metadata.entry_points(group=ENTRY_POINT_GROUP)
    except (TypeError, AttributeError, KeyError) as e:
        logger.debug(f"No entry points found or error accessing them: {e}")
        return loaded_count

    for ep in entry_points:
        try:
            plugin_class = ep.load()

            if not _validate_plugin_class(ep, plugin_class):
                continue

            plugin_type = cast("type[BaseToolPlugin]", plugin_class)

            # Instantiate once to resolve the definition name. This doubles as
            # the probe that surfaces any error raised on construction, keeping
            # a broken plugin from taking down discovery.
            instance = plugin_type()
            name = instance.definition.name.lower()

            # Builtins are discovered first and always win a name collision so a
            # third-party plugin can never silently shadow a curated core tool.
            if ToolRegistry.is_registered(name):
                logger.warning(
                    f"Plugin {ep.name!r} defines tool {name!r}, which is already "
                    f"registered (origin: {ToolRegistry.get_origin(name)}); "
                    "skipping the external plugin to avoid shadowing it",
                )
                continue

            origin = _entry_point_origin(ep)
            ToolRegistry.register(plugin_type, origin=origin, instance=instance)
            logger.info(f"Loaded external plugin: {name} (from {origin})")
            loaded_count += 1

        except Exception as e:  # noqa: BLE001 - isolate any misbehaving plugin
            logger.warning(
                f"Failed to load plugin {ep.name!r}: {type(e).__name__}: {e}",
            )

    logger.debug(f"Loaded {loaded_count} external plugins")
    return loaded_count


def discover_all_tools(force: bool = False) -> int:
    """Discover and register all available tools.

    This function loads both builtin tools and external plugins.
    It's safe to call multiple times - subsequent calls are no-ops
    unless force=True.

    Args:
        force: If True, re-discover even if already discovered.

    Returns:
        Total number of tools loaded.

    Example:
        >>> from lintro.plugins.discovery import discover_all_tools
        >>> count = discover_all_tools()
        >>> print(f"Loaded {count} tools")
    """
    global _discovered

    if _discovered and not force:
        logger.debug("Tools already discovered, skipping")
        return 0

    logger.debug("Discovering tools...")

    # Discover builtin tools first
    builtin_count = discover_builtin_tools()

    # Then discover external plugins (skips already-registered tool names)
    external_count = discover_external_plugins()

    total = builtin_count + external_count
    _discovered = True

    logger.info(
        f"Tool discovery complete: {builtin_count} builtin, {external_count} external",
    )
    return total


def is_discovered() -> bool:
    """Check if tool discovery has been performed.

    Returns:
        True if discover_all_tools() has been called, False otherwise.
    """
    return _discovered


def reset_discovery() -> None:
    """Reset the discovery state.

    This is primarily useful for testing.
    """
    global _discovered
    _discovered = False
