"""Discovery of the in-tree AI provider plugins.

ADR-0009 leaves "where the registration imports live" to the migration phase
(#2307); this module is that answer. Discovery derives the package name from
:class:`~lintro.ai.provider_enum.AIProvider` — a provider named ``x`` lives in
``lintro.ai.providers.x`` — so adding a vendor means adding an enum member and
a package, never editing a central import or class map. That is the point of
the plugin seam: the dict of import paths this replaced is not re-created here
under another name.

Importing a provider package registers its plugin and costs nothing beyond the
plugin and metadata modules; the vendor SDK is imported only when a plugin's
``build`` runs. So discovering all providers to serve one is as cheap as the
pre-migration lazy import of the single selected one.
"""

from __future__ import annotations

import importlib

from lintro.ai.provider_enum import AIProvider
from lintro.ai.providers.registry import is_registered, register_provider

__all__ = ["PROVIDER_PACKAGE_PREFIX", "load_builtin_providers"]

#: Import path every in-tree provider package sits under.
PROVIDER_PACKAGE_PREFIX = "lintro.ai.providers"

#: Module attribute each provider package exposes its registered plugin as.
_PLUGIN_ATTR = "PLUGIN"


def load_builtin_providers() -> None:
    """Import every in-tree provider package so its plugin is registered.

    Idempotent and cheap to call on every :func:`lintro.ai.providers.get_provider`.
    A provider already registered is skipped; a provider whose package was
    imported earlier in the process but has since been dropped from the
    registry (tests do this via
    :func:`~lintro.ai.providers.registry.clear_registered`) is re-registered
    from the package's ``PLUGIN`` attribute, because a second ``import`` of an
    already-imported module runs no registration.

    An enum member with no package of its own is skipped rather than raised
    on, so the "recognized but not implemented" path in
    :func:`lintro.ai.providers.get_provider` stays reachable. A
    :class:`ModuleNotFoundError` raised from *inside* a provider package is a
    real broken import and propagates.

    Raises:
        ModuleNotFoundError: If a provider package exists but one of its own
            imports is missing.
    """
    for provider in AIProvider:
        if is_registered(provider):
            continue
        package = f"{PROVIDER_PACKAGE_PREFIX}.{provider.value}"
        try:
            # Safe: the name is built from an AIProvider member and lintro's
            # own package prefix, never from user input.
            module = importlib.import_module(package)  # nosemgrep: non-literal-import
        except ModuleNotFoundError as exc:
            if exc.name != package:
                raise
            continue
        plugin = getattr(module, _PLUGIN_ATTR, None)
        if plugin is None or is_registered(provider):
            continue
        register_provider(plugin)
