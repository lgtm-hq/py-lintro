"""Cheap lookup of the tool names contributed by installed plugins.

Configuration parsing has to know which ``tools:`` keys name an externally
installed plugin, otherwise config written for one is reported as an unknown
tool and dropped (#1757). Reading that from
:mod:`lintro.plugins.discovery` made ``lintro.config`` import ``lintro.plugins``
— a layering violation that had to be carried as an ``ignore_imports`` entry
(#2290) and that pulled the whole plugin subsystem into CLI cold start (#1305).

This module owns the cheap half of the lookup instead: entry-point *metadata*,
which needs nothing above the ``utils`` layer. The authoritative half — names
already present in the tool registry — is contributed the other way round, by
:mod:`lintro.plugins.discovery` registering a source here at its own import
time. Config therefore never imports plugins, and still sees registry names
whenever the plugin subsystem is loaded at all.
"""

from __future__ import annotations

import importlib.metadata
from collections.abc import Callable, Iterable
from functools import lru_cache

from loguru import logger

# Entry-point group third-party packages advertise a tool plugin under.
ENTRY_POINT_GROUP = "lintro.tools"

# Pre-1.0 spelling of the group. Names advertised under it are still read here,
# silently; `lintro.plugins.discovery` is what warns, and only when it actually
# loads a plugin from the legacy group.
LEGACY_ENTRY_POINT_GROUP = "lintro.plugins"

# Sources registered by higher layers, called on every lookup. Populated by
# `lintro.plugins.discovery` when (and only when) that module is imported.
_EXTRA_NAME_SOURCES: list[Callable[[], Iterable[str]]] = []


def register_tool_name_source(
    source: Callable[[], Iterable[str]],
) -> None:
    """Register a callable contributing extra plugin tool names.

    Registration is idempotent so a module re-import cannot stack duplicate
    sources.

    Args:
        source: Zero-argument callable returning tool names. It must be cheap:
            it is called on every configuration parse.
    """
    if source not in _EXTRA_NAME_SOURCES:
        _EXTRA_NAME_SOURCES.append(source)


@lru_cache(maxsize=1)
def advertised_plugin_tool_names() -> frozenset[str]:
    """Read the tool names advertised by installed plugin entry points.

    Only the entry-point *metadata* is read: no plugin module is imported and
    no plugin class is instantiated, so this stays cheap enough to call from
    config parsing. The result is cached for the process lifetime because
    installed distributions cannot change while lintro is running; call
    :func:`reset_plugin_tool_name_cache` to drop the cache in tests.

    Returns:
        frozenset[str]: Lowercased entry-point names from both the current and
        the legacy plugin entry-point groups.
    """
    names: set[str] = set()
    for group in (ENTRY_POINT_GROUP, LEGACY_ENTRY_POINT_GROUP):
        try:
            entry_points = importlib.metadata.entry_points(group=group)
        except Exception as e:
            # This runs inside config loading. A broken metadata backend (an
            # unreadable dist-info directory, a third-party finder raising)
            # must degrade to "no plugin names known", never take the whole
            # configuration down with it.
            logger.debug(f"Could not read {group!r} entry points: {e}")
            continue
        for ep in entry_points:
            name = str(getattr(ep, "name", "") or "").strip().lower()
            if name:
                names.add(name)
    return frozenset(names)


def known_plugin_tool_names() -> frozenset[str]:
    """Return every tool name known to come from a plugin.

    Combines the advertised entry-point names with whatever registered sources
    contribute (see :func:`register_tool_name_source`). Builtin tool names may
    appear via a registered source; callers that care about the distinction
    should union this with the builtin names they already know.

    Returns:
        frozenset[str]: Lowercased plugin tool names.
    """
    names: set[str] = set(advertised_plugin_tool_names())
    for source in _EXTRA_NAME_SOURCES:
        names.update(source())
    return frozenset(names)


def reset_plugin_tool_name_cache() -> None:
    """Drop the cached entry-point metadata lookup.

    Primarily useful for tests that install fake entry points.
    """
    advertised_plugin_tool_names.cache_clear()
