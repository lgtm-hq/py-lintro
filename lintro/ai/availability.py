"""Transport-aware AI availability checks.

These are *presence* checks — is the SDK importable, is the binary on ``PATH``,
is the API-key variable set. Presence is only the first link of the chain lintro
gates AI work on::

    is_provider_available()  ->  check_liveness()  ->  invoke

A present credential is not a working one: a depleted account passes every check
in this module and fails every real call (#1826). The liveness step lives in
:mod:`lintro.ai.liveness` and is re-exported here so the whole chain is reachable
from one import.
"""

from __future__ import annotations

import click

from lintro.ai.enums import AITransport
from lintro.ai.liveness import (
    LivenessResult,
    LivenessState,
    check_liveness_sync,
)
from lintro.ai.provider_enum import AIProvider
from lintro.ai.registry import metadata_for

__all__ = [
    "LivenessResult",
    "LivenessState",
    "check_liveness_sync",
    "is_ai_available",
    "is_provider_available",
    "provider_api_key_env",
    "provider_cli_binary",
    "require_ai",
    "reset_availability_cache",
]

_AI_AVAILABLE: bool | None = None


def _resolve_provider(provider: AIProvider | str) -> AIProvider | None:
    if isinstance(provider, AIProvider):
        return provider
    try:
        return AIProvider(str(provider).lower())
    except ValueError:
        return None


def _resolve_transport(transport: AITransport | str | None) -> AITransport | None:
    if transport is None:
        return None
    if isinstance(transport, AITransport):
        return transport
    try:
        return AITransport(str(transport).lower())
    except ValueError:
        return None


def _api_provider_available(provider: AIProvider) -> bool:
    """Report whether the provider's API transport can be served here.

    Args:
        provider: The provider to check.

    Returns:
        True when the provider serves an API transport and its SDK imports.
    """
    import importlib

    metadata = metadata_for(provider)
    if not metadata.supports(AITransport.API) or metadata.sdk_package is None:
        return False
    try:
        # Safe: the name comes from lintro's own provider metadata, never from
        # user input.
        importlib.import_module(metadata.sdk_package)  # nosemgrep: non-literal-import
    except ImportError:
        return False
    return True


def _cli_binary_available(provider: AIProvider) -> bool:
    """Report whether the provider's CLI binary is on ``PATH``.

    Args:
        provider: The provider to check.

    Returns:
        True when the provider declares a CLI binary and it resolves.
    """
    import shutil

    binary = provider_cli_binary(provider)
    if binary is None:
        return False
    return shutil.which(binary) is not None


def is_provider_available(
    provider: AIProvider | str,
    *,
    transport: AITransport | str | None = None,
) -> bool:
    """Check if a provider is usable for the given transport.

    Args:
        provider: Provider name or enum member.
        transport: Optional transport filter. When ``None``, either API or CLI
            availability satisfies the check.

    Returns:
        bool: True when the provider can serve requests.
    """
    from loguru import logger

    provider_enum = _resolve_provider(provider)
    if provider_enum is None:
        supported = ", ".join(p.value for p in AIProvider)
        logger.warning(
            "Unknown AI provider {!r}; supported providers: {}",
            provider,
            supported,
        )
        return False

    transport_enum = _resolve_transport(transport)
    if transport_enum == AITransport.API:
        return _api_provider_available(provider_enum)
    if transport_enum == AITransport.CLI:
        return _cli_binary_available(provider_enum)

    return _api_provider_available(provider_enum) or _cli_binary_available(
        provider_enum,
    )


def is_ai_available() -> bool:
    """Check if at least one AI provider is usable.

    Returns:
        bool: True when any provider is available via API or CLI.
    """
    global _AI_AVAILABLE

    if _AI_AVAILABLE is not None:
        return _AI_AVAILABLE

    for provider in AIProvider:
        if is_provider_available(provider):
            _AI_AVAILABLE = True
            return True

    _AI_AVAILABLE = False
    return False


def require_ai() -> None:
    """Ensure AI dependencies are installed.

    Raises:
        click.UsageError: If no AI provider packages are installed,
            with installation instructions.
    """
    if not is_ai_available():
        raise click.UsageError(
            "AI features require lintro[ai]. Install with: uv pip install 'lintro[ai]'",
        )


def reset_availability_cache() -> None:
    """Reset the cached availability check.

    Useful for testing when mocking imports.
    """
    global _AI_AVAILABLE
    _AI_AVAILABLE = None


def provider_api_key_env(provider: AIProvider) -> str:
    """Return the default API key environment variable for a provider.

    Args:
        provider: The provider to look up.

    Returns:
        The variable name declared by the provider's plugin metadata.
    """
    return metadata_for(provider).default_api_key_env


def provider_cli_binary(provider: AIProvider) -> str | None:
    """Return the CLI binary name for a provider, if any.

    Args:
        provider: The provider to look up.

    Returns:
        The binary name declared by the provider's plugin metadata, or None
        when the provider serves no CLI transport.
    """
    metadata = metadata_for(provider)
    if not metadata.supports(AITransport.CLI):
        return None
    return metadata.cli_binary
