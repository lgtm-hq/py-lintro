"""In-tree registry of AI provider plugins.

Holds the :class:`~lintro.ai.providers.protocol.ProviderPlugin` objects that
providers register themselves as, keyed by
:class:`~lintro.ai.provider_enum.AIProvider`. Discovery is in-tree only: a
plugin appears here because its module ran ``register_provider`` at import
time, not because an entry point advertised it (see the non-goals in
``docs/adr/0009-ai-provider-plugin-contract.md``).

Nothing registers yet. :func:`lintro.ai.providers.get_provider` still resolves
providers through its own class map, and swapping that map for this registry is
the follow-up phase. Registering here today changes no product behaviour.

Example:
    >>> from lintro.ai.providers.registry import (
    ...     all_providers,
    ...     get_registered,
    ...     register_provider,
    ... )
    >>> register_provider(MyPlugin())  # doctest: +SKIP
    >>> get_registered("anthropic")  # doctest: +SKIP
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

from lintro.ai.exceptions import (
    AIProviderAlreadyRegisteredError,
    AIProviderNotRegisteredError,
)
from lintro.ai.provider_enum import AIProvider, accepted_provider_values

if TYPE_CHECKING:
    from lintro.ai.providers.protocol import ProviderPlugin

__all__ = [
    "all_providers",
    "clear_registered",
    "get_registered",
    "is_registered",
    "register_provider",
    "restore_registered",
]

_PLUGINS: dict[AIProvider, ProviderPlugin] = {}
_LOCK = threading.RLock()


def register_provider(plugin: ProviderPlugin) -> ProviderPlugin:
    """Register one provider plugin under its declared name.

    Usable as a decorator on a plugin factory or called directly with an
    instance. Registration is keyed by ``plugin.name``, so a provider is
    registered at most once per process.

    Args:
        plugin: The plugin to register.

    Returns:
        The plugin, so the call can be used as a decorator.

    Raises:
        AIProviderAlreadyRegisteredError: If a plugin is already registered
            under the same name. Re-registration is an import-order bug, not a
            supported override; tests that need a clean slate call
            :func:`clear_registered`.
    """
    name = plugin.name
    with _LOCK:
        existing = _PLUGINS.get(name)
        if existing is not None:
            raise AIProviderAlreadyRegisteredError(
                f"AI provider '{name.value}' is already registered by "
                f"{type(existing).__module__}.{type(existing).__qualname__}; "
                f"{type(plugin).__module__}.{type(plugin).__qualname__} "
                "cannot register it again.",
            )
        _PLUGINS[name] = plugin
    return plugin


def get_registered(name: AIProvider | str) -> ProviderPlugin:
    """Look up a registered plugin by provider name.

    Args:
        name: Provider enum member, or the string a user typed.

    Returns:
        The registered plugin for *name*.

    Raises:
        AIProviderNotRegisteredError: If *name* is not a known provider, or is
            known but has no plugin registered. The message names what is
            registered so the caller can tell the two cases apart.
    """
    try:
        provider = AIProvider(str(name).lower())
    except ValueError as exc:
        raise AIProviderNotRegisteredError(
            f"Unknown AI provider: '{name}'. "
            f"Accepted providers: {accepted_provider_values()}.",
        ) from exc
    with _LOCK:
        plugin = _PLUGINS.get(provider)
    if plugin is None:
        raise AIProviderNotRegisteredError(
            f"AI provider '{provider.value}' has no registered plugin. "
            f"Registered providers: {_registered_names() or 'none'}.",
        )
    return plugin


def all_providers() -> dict[AIProvider, ProviderPlugin]:
    """Return every registered plugin.

    Returns:
        A copy of the registry keyed by provider, in enum declaration order so
        callers never depend on registration (import) order.
    """
    with _LOCK:
        return {
            provider: _PLUGINS[provider]
            for provider in AIProvider
            if provider in _PLUGINS
        }


def is_registered(name: AIProvider | str) -> bool:
    """Report whether a plugin is registered for a provider name.

    Args:
        name: Provider enum member, or the string a user typed.

    Returns:
        True when *name* is a known provider with a registered plugin.
    """
    try:
        provider = AIProvider(str(name).lower())
    except ValueError:
        return False
    with _LOCK:
        return provider in _PLUGINS


def clear_registered() -> None:
    """Drop every registered plugin.

    Exists for tests that install a fake plugin; production code never clears
    the registry. Pair it with :func:`restore_registered` to put the real
    plugins back.
    """
    with _LOCK:
        _PLUGINS.clear()


def restore_registered(plugins: dict[AIProvider, ProviderPlugin]) -> None:
    """Replace the registry contents with *plugins*.

    Args:
        plugins: Mapping previously obtained from :func:`all_providers`.
    """
    with _LOCK:
        _PLUGINS.clear()
        _PLUGINS.update(plugins)


def _registered_names() -> str:
    """Return the registered provider names for error messages.

    Returns:
        Comma-separated provider names in enum declaration order, or an empty
        string when nothing is registered.
    """
    return ", ".join(provider.value for provider in AIProvider if provider in _PLUGINS)
