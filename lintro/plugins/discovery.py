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
import os
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

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

# Previously documented group name, still honored so plugins packaged against
# the old docs keep working after an upgrade. Deprecated: emits a warning.
LEGACY_ENTRY_POINT_GROUP = "lintro.plugins"

# Attributes a plugin class must expose to satisfy the LintroPlugin contract.
_REQUIRED_PLUGIN_ATTRS = ("definition", "check", "fix", "set_options")

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


def _normalize_trust_name(name: str) -> str:
    """Normalize an entry-point or distribution name for trust comparison.

    Applies PEP 503-style normalization (lowercase, runs of ``-``, ``_`` and
    ``.`` collapsed to a single ``-``) so that a configured trusted name such
    as ``My_Plugin`` matches installed metadata spelled ``my-plugin``.

    Args:
        name: The raw name to normalize.

    Returns:
        The normalized name.
    """
    return re.sub(r"[-_.]+", "-", name).strip().lower()


def _load_plugins_config() -> dict[str, Any] | None:
    """Load the ``plugins`` configuration section for external plugin trust.

    Reads the ``plugins`` mapping from ``.lintro-config.yaml`` if present.
    When the primary config has no ``plugins`` section, falls back to
    ``[tool.lintro.plugins]`` in ``pyproject.toml`` so mixed-config setups
    (YAML for general settings, pyproject for the trust allowlist) are honored.
    This is intentionally lightweight and independent of the full config
    loader so plugin discovery never triggers heavier config parsing.

    Returns:
        The raw ``plugins`` mapping, an empty dict when none is configured, or
        ``None`` when a config file exists but could not be read. Callers must
        treat ``None`` as a signal to fail closed (load no external plugins)
        rather than as an absent allowlist.
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
            if isinstance(plugins, dict):
                return plugins

        # No plugins section in the primary config (or no primary config):
        # consult the pyproject fallback before concluding none is configured.
        pyproject_data, _ = _load_pyproject_fallback()
        plugins = pyproject_data.get("plugins")
        return plugins if isinstance(plugins, dict) else {}
    except (OSError, ValueError, ImportError) as e:
        # A config file exists but is unreadable/malformed. Return None so the
        # caller fails closed instead of silently treating the allowlist as
        # absent (which under env opt-in would load every discovered plugin).
        logger.warning(f"Could not read plugins trust config; failing closed: {e}")
        return None


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
    if plugins_cfg is None:
        # Trust config exists but could not be read: fail closed and load no
        # external plugins, even if env opt-in is set.
        logger.warning(
            "Plugins trust config could not be read; failing closed and "
            "loading no external plugins.",
        )
        return False, frozenset()

    if plugins_cfg:
        raw_trusted = plugins_cfg.get("trusted")
        if isinstance(raw_trusted, str):
            raw_trusted = [raw_trusted]
        if isinstance(raw_trusted, list):
            # Presence of a trusted allowlist is itself an explicit opt-in.
            # Names are normalized so case/separator spelling differences
            # between config and installed metadata still match.
            trusted = frozenset(
                _normalize_trust_name(str(name)) for name in raw_trusted
            )
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
    if _normalize_trust_name(str(ep.name)) in trusted:
        return True
    dist = getattr(ep, "dist", None)
    dist_name = getattr(dist, "name", None)
    return (
        isinstance(dist_name, str)
        and _normalize_trust_name(dist_name) in trusted
    )


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

    The legacy ``lintro.plugins`` group (documented before this group was
    renamed) is still scanned so already-installed plugins keep working; a
    deprecation warning is logged for each plugin found there. An entry point
    advertised under both groups is loaded once.

    Each entry point is loaded, validated against the public plugin contract,
    checked for API compatibility, and registered. Failure is fully isolated:
    a plugin that fails to import, is malformed, declares an incompatible API
    version, collides with a builtin name, or raises on instantiation is logged
    and skipped without affecting the other plugins or crashing lintro.

    Returns:
        Number of external plugins successfully loaded.
    """
    loaded_count = 0

    # External plugin loading is opt-in and default-deny. Resolve the trust
    # decision before touching the entry-point registry so a default install
    # never imports third-party plugin code.
    enabled, trusted = _resolve_plugin_trust()
    if not enabled:
        logger.debug(
            "External plugin loading is disabled (default). Set "
            f"{ENV_ENABLE_EXTERNAL_PLUGINS}=1 or configure a [tool.lintro] "
            "plugins allowlist to enable it.",
        )
        return loaded_count

    grouped: list[tuple[str, tuple[EntryPoint, ...]]] = []
    for group in (ENTRY_POINT_GROUP, LEGACY_ENTRY_POINT_GROUP):
        try:
            grouped.append(
                (group, tuple(importlib.metadata.entry_points(group=group))),
            )
        except (TypeError, AttributeError, KeyError) as e:
            logger.debug(f"No entry points found or error accessing them: {e}")

    seen: set[tuple[str, str]] = set()
    for group, entry_points in grouped:
        for ep in entry_points:
            key = (str(ep.name), str(getattr(ep, "value", "") or ""))
            if key in seen:
                continue
            seen.add(key)
            if not _is_trusted_entry_point(ep=ep, trusted=trusted):
                logger.info(
                    f"Skipping untrusted external plugin {ep.name!r} "
                    "(not in the configured plugins.trusted allowlist)",
                )
                continue
            if group == LEGACY_ENTRY_POINT_GROUP:
                logger.warning(
                    f"Plugin {ep.name!r} registers via the deprecated "
                    f"{LEGACY_ENTRY_POINT_GROUP!r} entry-point group; update the "
                    f"package to use {ENTRY_POINT_GROUP!r}.",
                )
            loaded_count += _load_external_entry_point(ep=ep)

    logger.debug(f"Loaded {loaded_count} external plugins")
    return loaded_count


def _load_external_entry_point(*, ep: EntryPoint) -> int:
    """Load, validate, and register a single external plugin entry point.

    Args:
        ep: The entry point to load.

    Returns:
        ``1`` when the plugin was registered, ``0`` when it was skipped.
    """
    try:
        plugin_class = ep.load()

        if not _validate_plugin_class(ep, plugin_class):
            return 0

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
            return 0

        origin = _entry_point_origin(ep)
        ToolRegistry.register(plugin_type, origin=origin, instance=instance)
        logger.info(f"Loaded external plugin: {name} (from {origin})")
        return 1

    except Exception as e:  # noqa: BLE001 - isolate any misbehaving plugin
        logger.warning(
            f"Failed to load plugin {ep.name!r}: {type(e).__name__}: {e}",
        )
        return 0


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
