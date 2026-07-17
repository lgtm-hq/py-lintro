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

import importlib.metadata
from pathlib import Path
from typing import TYPE_CHECKING, cast

from lintro.utils.lazy_logger import logger

if TYPE_CHECKING:
    from importlib.metadata import EntryPoint

    from lintro.plugins.base import BaseToolPlugin

# Path to builtin tool definitions
BUILTIN_DEFINITIONS_PATH = Path(__file__).parent.parent / "tools" / "definitions"

# Canonical tool name -> definition module path. Import is deferred until the
# tool is selected (or list-tools / get_all materializes everything).
BUILTIN_TOOL_MODULES: dict[str, str] = {
    "actionlint": "lintro.tools.definitions.actionlint",
    "astro-check": "lintro.tools.definitions.astro_check",
    "bandit": "lintro.tools.definitions.bandit",
    "black": "lintro.tools.definitions.black",
    "cargo_audit": "lintro.tools.definitions.cargo_audit",
    "cargo_deny": "lintro.tools.definitions.cargo_deny",
    "clippy": "lintro.tools.definitions.clippy",
    "commitlint": "lintro.tools.definitions.commitlint",
    "dotenv_linter": "lintro.tools.definitions.dotenv_linter",
    "gitleaks": "lintro.tools.definitions.gitleaks",
    "hadolint": "lintro.tools.definitions.hadolint",
    "idiom-review": "lintro.tools.definitions.idiom_review",
    "markdownlint": "lintro.tools.definitions.markdownlint",
    "mypy": "lintro.tools.definitions.mypy",
    "osv_scanner": "lintro.tools.definitions.osv_scanner",
    "oxfmt": "lintro.tools.definitions.oxfmt",
    "oxlint": "lintro.tools.definitions.oxlint",
    "prettier": "lintro.tools.definitions.prettier",
    "pydoclint": "lintro.tools.definitions.pydoclint",
    "pytest": "lintro.tools.definitions.pytest",
    "ruff": "lintro.tools.definitions.ruff",
    "rustfmt": "lintro.tools.definitions.rustfmt",
    "semgrep": "lintro.tools.definitions.semgrep",
    "shellcheck": "lintro.tools.definitions.shellcheck",
    "shfmt": "lintro.tools.definitions.shfmt",
    "sqlfluff": "lintro.tools.definitions.sqlfluff",
    "stylelint": "lintro.tools.definitions.stylelint",
    "svelte-check": "lintro.tools.definitions.svelte_check",
    "taplo": "lintro.tools.definitions.taplo",
    "tsc": "lintro.tools.definitions.tsc",
    "vale": "lintro.tools.definitions.vale",
    "vue-tsc": "lintro.tools.definitions.vue_tsc",
    "yamllint": "lintro.tools.definitions.yamllint",
}

# Entry point group third-party packages use to register tool plugins.
ENTRY_POINT_GROUP = "lintro.tools"

# Previously documented group name, still honored so plugins packaged against
# the old docs keep working after an upgrade. Deprecated: emits a warning.
LEGACY_ENTRY_POINT_GROUP = "lintro.plugins"

# Attributes a plugin class must expose to satisfy the LintroPlugin contract.
_REQUIRED_PLUGIN_ATTRS = ("definition", "check", "fix", "set_options")

# Track whether discovery has been performed
_discovered: bool = False


def discover_builtin_tools() -> int:
    """Register builtin tools as deferred name -> module mappings.

    Modules are imported only when a tool is first accessed (or when
    ``get_all`` / ``list-tools`` materializes every tool).

    Returns:
        Number of builtin tools registered for deferred import.

    Note:
        Each tool definition file should use the @register_tool decorator
        so importing the module registers the live plugin class.
    """
    from lintro.plugins.registry import ToolRegistry

    if not BUILTIN_DEFINITIONS_PATH.exists():
        logger.warning(
            f"Builtin definitions path not found: {BUILTIN_DEFINITIONS_PATH}",
        )
        return 0

    for name, module_path in BUILTIN_TOOL_MODULES.items():
        ToolRegistry.register_deferred(
            name=name,
            module_path=module_path,
            origin=ToolRegistry.BUILTIN_ORIGIN,
        )

    loaded_count = len(BUILTIN_TOOL_MODULES)
    logger.debug(f"Registered {loaded_count} deferred builtin tool definitions")
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

    from lintro.plugins.protocol import (
        LINTRO_PLUGIN_API_VERSION,
        is_compatible_api_version,
    )

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
    from lintro.plugins.registry import ToolRegistry

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
