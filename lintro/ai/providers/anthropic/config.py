"""Anthropic-specific AI settings (#2309).

Declared next to
:data:`~lintro.ai.providers.anthropic.metadata.ANTHROPIC_METADATA` so the
``--bare`` / auth-mode policy lives in the Anthropic package rather than on the
shared :class:`~lintro.ai.config.AIConfig`.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import TYPE_CHECKING, ClassVar

from pydantic import Field

from lintro.ai.enums import CliBareMode
from lintro.ai.provider_config import ProviderConfig
from lintro.ai.provider_enum import AIProvider

if TYPE_CHECKING:
    from collections.abc import Mapping

    from lintro.ai.config import AIConfig

__all__ = ["AnthropicConfig", "anthropic_settings"]


class AnthropicConfig(ProviderConfig):
    """Settings only the Anthropic provider reads."""

    legacy_keys: ClassVar[Mapping[str, str]] = MappingProxyType(
        {"cli_bare": "cli_bare"},
    )

    cli_bare: CliBareMode = Field(
        default=CliBareMode.AUTO,
        description=(
            "Whether the anthropic CLI transport passes '--bare' to the "
            "'claude' binary. '--bare' drops the CLI's agentic tool surface "
            "but also disables OAuth session login, so it only authenticates "
            "against an API key. 'auto' (default) sends it only when an API "
            "key is reachable (ANTHROPIC_API_KEY or a configured "
            "apiKeyHelper), so subscription logins keep working; 'always' and "
            "'never' force the choice. Overridable per run with the "
            "LINTRO_CLI_BARE environment variable."
        ),
    )


def anthropic_settings(config: AIConfig) -> AnthropicConfig:
    """Return the effective Anthropic block from *config*.

    Args:
        config: Effective AI configuration for this run.

    Returns:
        The resolved ``ai.providers.anthropic`` block, or a default-valued one
        when the config declares none.
    """
    block = config.provider_settings(AIProvider.ANTHROPIC)
    return block if isinstance(block, AnthropicConfig) else AnthropicConfig()
