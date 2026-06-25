"""Transport-aware AI availability checks."""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

import click

from lintro.ai.enums import AITransport
from lintro.ai.registry import AIProvider

if TYPE_CHECKING:
    pass

_AI_AVAILABLE: bool | None = None

_CLI_BINARIES: dict[tuple[AIProvider, AITransport], str] = {
    (AIProvider.ANTHROPIC, AITransport.CLI): "claude",
    (AIProvider.OPENAI, AITransport.CLI): "codex",
    (AIProvider.CURSOR, AITransport.CLI): "agent",
}


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
    if provider == AIProvider.CURSOR:
        return False
    try:
        if provider == AIProvider.ANTHROPIC:
            import anthropic  # noqa: F401

            return True
        if provider == AIProvider.OPENAI:
            import openai  # noqa: F401

            return True
    except ImportError:
        return False
    return False


def _cli_binary_available(provider: AIProvider) -> bool:
    import shutil

    binary = _CLI_BINARIES.get((provider, AITransport.CLI))
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
            "AI features require lintro[ai]. "
            "Install with: uv pip install 'lintro[ai]'",
        )


def reset_availability_cache() -> None:
    """Reset the cached availability check.

    Useful for testing when mocking imports.
    """
    global _AI_AVAILABLE
    _AI_AVAILABLE = None


def provider_api_key_env(provider: AIProvider) -> str:
    """Return the default API key environment variable for a provider."""
    from lintro.ai.registry import PROVIDERS

    return PROVIDERS.get(provider).default_api_key_env


def provider_cli_binary(provider: AIProvider) -> str | None:
    """Return the CLI binary name for a provider, if any."""
    return _CLI_BINARIES.get((provider, AITransport.CLI))


def codex_auth_configured() -> bool:
    """Return True when Codex CLI auth is likely configured."""
    if os.environ.get("CODEX_API_KEY"):
        return True
    return (Path.home() / ".codex" / "auth.json").is_file()
