"""Graceful degradation for AI dependencies.

Checks whether the required AI provider packages are installed and
provides clear error messages when they are not.
"""

from __future__ import annotations

import click

_AI_AVAILABLE: bool | None = None


def is_ai_available() -> bool:
    """Check if at least one AI provider package is installed.

    Returns:
        bool: True if anthropic or openai is importable.
    """
    global _AI_AVAILABLE

    if _AI_AVAILABLE is not None:
        return _AI_AVAILABLE

    try:
        import anthropic  # noqa: F401

        _AI_AVAILABLE = True
        return True
    except ImportError:
        pass

    try:
        import openai  # noqa: F401

        _AI_AVAILABLE = True
        return True
    except ImportError:
        pass

    _AI_AVAILABLE = False
    return False


def is_provider_available(provider: str) -> bool:
    """Check if a specific provider package is installed.

    Args:
        provider: Provider name ("anthropic" or "openai").

    Returns:
        bool: True if the provider's package is importable.
    """
    try:
        if provider == "anthropic":
            import anthropic  # noqa: F401

            return True
        elif provider == "openai":
            import openai  # noqa: F401

            return True
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
