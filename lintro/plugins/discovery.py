"""Tool discovery for builtin and external plugins.

This module handles discovering and loading Lintro tools from:
1. Built-in tool definitions (lintro/tools/definitions/)
2. External plugins via Python entry points (lintro.plugins)

Example:
    >>> from lintro.plugins.discovery import discover_all_tools
    >>> discover_all_tools()  # Loads all available tools
"""

from __future__ import annotations

import importlib
import importlib.metadata
import os
from pathlib import Path
from typing import Any, cast

from loguru import logger

from lintro.plugins.base import BaseToolPlugin
from lintro.plugins.registry import ToolRegistry

# Path to builtin tool definitions
BUILTIN_DEFINITIONS_PATH = Path(__file__).parent.parent / "tools" / "definitions"

# Entry point group for external plugins
ENTRY_POINT_GROUP = "lintro.plugins"

# Environment variable that opts in to loading external (third-party) plugins.
# Truthy values: "1", "true", "yes", "on" (case-insensitive).
ENV_ENABLE_EXTERNAL_PLUGINS = "LINTRO_ENABLE_EXTERNAL_PLUGINS"

# Truthy string values accepted for the opt-in environment variable.
_TRUTHY_ENV_VALUES = frozenset({"1", "true", "yes", "on"})

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


def _load_plugins_config() -> dict[str, Any]:
    """Load the ``plugins`` configuration section for external plugin trust.

    Reads the ``plugins`` mapping from ``.lintro-config.yaml`` if present,
    otherwise falls back to ``[tool.lintro.plugins]`` in ``pyproject.toml``.
    This is intentionally lightweight and independent of the full config
    loader so plugin discovery never triggers heavier config parsing.

    Returns:
        The raw ``plugins`` mapping, or an empty dict if none is configured.
    """
    # Imported lazily to avoid pulling config parsing into module import.
    from lintro.config.config_loader import (
        _find_config_file,
        _load_pyproject_fallback,
        _load_yaml_file,
    )

    try:
        found_path = _find_config_file()
        if found_path is not None:
            data = _load_yaml_file(found_path)
            plugins = data.get("plugins")
            return plugins if isinstance(plugins, dict) else {}

        pyproject_data, _ = _load_pyproject_fallback()
        plugins = pyproject_data.get("plugins")
        return plugins if isinstance(plugins, dict) else {}
    except (OSError, ValueError, ImportError) as e:
        logger.debug(f"Could not read plugins config: {e}")
        return {}


def _resolve_plugin_trust() -> tuple[bool, frozenset[str] | None]:
    """Resolve whether external plugins are enabled and which are trusted.

    External plugin loading is opt-in and default-deny. It is enabled when
    either of the following is true:

    - The ``LINTRO_ENABLE_EXTERNAL_PLUGINS`` environment variable is set to a
      truthy value (``1``/``true``/``yes``/``on``).
    - A ``plugins`` config section opts in, via ``enabled: true`` and/or a
      ``trusted`` allowlist of entry-point or distribution names.

    When a ``trusted`` allowlist is configured, only entry points whose name
    or distribution is in the list are loaded, regardless of how loading was
    enabled. When no allowlist is configured, all discovered entry points are
    eligible once loading is enabled.

    Returns:
        A tuple ``(enabled, trusted)`` where ``enabled`` reports whether any
        external plugin loading is permitted and ``trusted`` is the allowlist
        of names (or ``None`` when no allowlist is configured).
    """
    env_value = os.environ.get(ENV_ENABLE_EXTERNAL_PLUGINS, "").strip().lower()
    env_enabled = env_value in _TRUTHY_ENV_VALUES

    config_enabled = False
    trusted: frozenset[str] | None = None

    plugins_cfg = _load_plugins_config()
    if plugins_cfg:
        raw_trusted = plugins_cfg.get("trusted")
        if isinstance(raw_trusted, str):
            raw_trusted = [raw_trusted]
        if isinstance(raw_trusted, list):
            # Presence of a trusted allowlist is itself an explicit opt-in.
            trusted = frozenset(str(name) for name in raw_trusted)
            config_enabled = True

        enabled_flag = plugins_cfg.get("enabled")
        if isinstance(enabled_flag, bool):
            config_enabled = config_enabled or enabled_flag

    return (env_enabled or config_enabled), trusted


def _is_trusted_entry_point(
    ep: importlib.metadata.EntryPoint,
    trusted: frozenset[str] | None,
) -> bool:
    """Check whether an entry point is permitted by the trust allowlist.

    Args:
        ep: The entry point being considered for loading.
        trusted: Allowlist of trusted entry-point or distribution names, or
            ``None`` when no allowlist is configured (all names permitted).

    Returns:
        True if the entry point may be loaded, False otherwise.
    """
    if trusted is None:
        return True
    if ep.name in trusted:
        return True
    dist = getattr(ep, "dist", None)
    dist_name = getattr(dist, "name", None)
    return isinstance(dist_name, str) and dist_name in trusted


def discover_external_plugins() -> int:
    """Load external plugins via entry points.

    External plugin loading is opt-in and default-deny: a default installation
    never imports or executes third-party plugin code at startup. Loading is
    enabled only via the ``LINTRO_ENABLE_EXTERNAL_PLUGINS`` environment
    variable or a ``plugins`` config section (see :func:`_resolve_plugin_trust`).

    External plugins can register themselves by defining an entry point
    in their pyproject.toml or setup.py:

        [project.entry-points."lintro.plugins"]
        my-tool = "my_package.plugin:MyToolPlugin"

    Returns:
        Number of external plugins loaded.

    Note:
        External plugins should be classes that implement LintroPlugin.
        They will be automatically registered with the ToolRegistry.
    """
    loaded_count = 0

    enabled, trusted = _resolve_plugin_trust()
    if not enabled:
        logger.debug(
            "External plugin loading is disabled (default). Set "
            f"{ENV_ENABLE_EXTERNAL_PLUGINS}=1 or configure a [tool.lintro] "
            "plugins allowlist to enable it.",
        )
        return loaded_count

    try:
        entry_points = importlib.metadata.entry_points(group=ENTRY_POINT_GROUP)
    except (TypeError, AttributeError, KeyError) as e:
        logger.debug(f"No entry points found or error accessing them: {e}")
        return loaded_count

    for ep in entry_points:
        if not _is_trusted_entry_point(ep=ep, trusted=trusted):
            logger.info(
                f"Skipping untrusted external plugin {ep.name!r} "
                "(not in the configured plugins.trusted allowlist)",
            )
            continue
        try:
            plugin_class = ep.load()

            # Validate that it's a proper plugin class
            if not isinstance(plugin_class, type):
                logger.warning(
                    f"Entry point {ep.name!r} does not point to a class, skipping",
                )
                continue

            # Check if it implements LintroPlugin protocol (without instantiating)
            # Check for required attributes since Protocol with properties
            # can't use issubclass reliably
            required_attrs = ("definition", "check", "fix", "set_options")
            if not all(hasattr(plugin_class, attr) for attr in required_attrs):
                logger.warning(
                    f"Entry point {ep.name!r} class does not implement LintroPlugin, "
                    "skipping",
                )
                continue

            # Register the plugin if not already registered
            if not ToolRegistry.is_registered(ep.name):
                ToolRegistry.register(cast(type[BaseToolPlugin], plugin_class))
                logger.info(f"Loaded external plugin: {ep.name}")
                loaded_count += 1
            else:
                logger.debug(f"Plugin {ep.name!r} already registered, skipping")

        except (ImportError, AttributeError, TypeError, RuntimeError) as e:
            logger.warning(f"Failed to load plugin {ep.name!r}: {e}")

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
