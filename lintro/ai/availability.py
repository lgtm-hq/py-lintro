"""Graceful degradation for AI dependencies.

Checks whether the required AI provider packages are installed and
provides clear error messages when they are not.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import click

if TYPE_CHECKING:
    from lintro.ai.registry import AIProvider

_AI_AVAILABLE: bool | None = None


def is_ai_available() -> bool:
    """Check if at least one AI provider is usable.

    Returns:
        bool: True if anthropic, openai, or the Cursor agent CLI is available.
    """
    global _AI_AVAILABLE

    if _AI_AVAILABLE is not None:
        return _AI_AVAILABLE

    if is_provider_available("cursor"):
        _AI_AVAILABLE = True
        return True

    try:
        import anthropic  # noqa: F401 -- import-only availability check

        _AI_AVAILABLE = True
        return True
    except ImportError:
        pass

    try:
        import openai  # noqa: F401 -- import-only availability check

        _AI_AVAILABLE = True
        return True
    except ImportError:
        pass

    _AI_AVAILABLE = False
    return False


def is_provider_available(provider: AIProvider | str) -> bool:
    """Check if a specific provider package is installed.

    Args:
        provider: Provider name ("anthropic" or "openai").

    Returns:
        bool: True if the provider's package is importable.
    """
    from lintro.ai.registry import AIProvider

    if isinstance(provider, AIProvider):
        provider_value = provider.value
    else:
        provider_value = str(provider).lower()

    if provider_value not in {p.value for p in AIProvider}:
        from loguru import logger

        supported = ", ".join(p.value for p in AIProvider)
        logger.warning(
            "Unknown AI provider {!r}; supported providers: {}",
            provider_value,
            supported,
        )
        return False

    try:
        if provider_value == AIProvider.ANTHROPIC.value:
            import anthropic  # noqa: F401 -- import-only availability check

            return True
        if provider_value == AIProvider.OPENAI.value:
            import openai  # noqa: F401 -- import-only availability check

            return True
        if provider_value == AIProvider.CURSOR.value:
            from lintro.ai.providers.cursor import _find_agent

            return _find_agent() is not None
    except ImportError:
        pass
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
