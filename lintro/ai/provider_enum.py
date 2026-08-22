"""AI provider enumeration.

Defines the ``AIProvider`` :class:`~enum.StrEnum` that identifies each
supported AI backend.  Extracted from :mod:`lintro.ai.registry` so that
lightweight modules can reference provider identities without pulling in
the full registry and its pricing data.
"""

from __future__ import annotations

from enum import StrEnum, auto


class AIProvider(StrEnum):
    """Supported AI providers."""

    ANTHROPIC = auto()
    OPENAI = auto()
    CURSOR = auto()


def accepted_provider_names() -> list[str]:
    """Return accepted provider names in alphabetical order.

    Returns:
        Sorted provider values suitable for help text and ``click.Choice``.
    """
    return sorted(member.value for member in AIProvider)


def accepted_provider_values() -> str:
    """Return the user-facing accepted-provider list, alphabetically.

    Returns:
        Comma-separated provider names with no implied ranking.
    """
    return ", ".join(accepted_provider_names())


def provider_required_error() -> str:
    """Return the migration error used when AI is on but no provider is set.

    Returns:
        A message naming the three ways to set ``ai.provider`` and the
        accepted providers in alphabetical order.
    """
    return (
        "ai.provider is required when ai.lint or ai.review is enabled. "
        "Set it via `ai.provider` in config, LINTRO_AI_PROVIDER, or --provider. "
        f"Accepted providers: {accepted_provider_values()}."
    )
