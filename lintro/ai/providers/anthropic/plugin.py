"""Anthropic provider plugin.

Implements :class:`~lintro.ai.providers.protocol.ProviderPlugin` for Anthropic.
The plugin itself never imports the ``anthropic`` SDK: :meth:`AnthropicPlugin.build`
imports :mod:`lintro.ai.providers.anthropic.provider` on demand, so registration
and metadata lookups stay as cheap as the pre-migration lazy factory import.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from lintro.ai.enums import AITransport
from lintro.ai.provider_enum import AIProvider
from lintro.ai.providers.anthropic.config import (
    AnthropicConfig,
    anthropic_settings,
)
from lintro.ai.providers.anthropic.metadata import ANTHROPIC_METADATA

if TYPE_CHECKING:
    from lintro.ai.config import AIConfig
    from lintro.ai.providers.base import BaseAIProvider
    from lintro.ai.providers.protocol import ProviderMetadata

__all__ = ["AnthropicPlugin"]


@dataclass(frozen=True, slots=True)
class AnthropicPlugin:
    """Builds the Anthropic provider from an effective :class:`AIConfig`."""

    @property
    def name(self) -> AIProvider:
        """Return the registry key for this plugin.

        Returns:
            :attr:`~lintro.ai.provider_enum.AIProvider.ANTHROPIC`.
        """
        return AIProvider.ANTHROPIC

    @property
    def transports(self) -> frozenset[AITransport]:
        """Return the transports Anthropic serves.

        Declared once, on the metadata record, so the transports a plugin
        advertises and the ones doctor and config validation read cannot
        disagree (#2308).

        Returns:
            :attr:`ANTHROPIC_METADATA.supported_transports`.
        """
        return ANTHROPIC_METADATA.supported_transports

    @property
    def metadata(self) -> ProviderMetadata:
        """Return the static Anthropic description.

        Returns:
            :data:`~lintro.ai.providers.anthropic.metadata.ANTHROPIC_METADATA`.
        """
        return ANTHROPIC_METADATA

    @property
    def config_model(self) -> type[AnthropicConfig]:
        """Return the model for the ``ai.providers.anthropic`` block.

        Returns:
            :class:`~lintro.ai.providers.anthropic.config.AnthropicConfig`.
        """
        return AnthropicConfig

    def build(self, config: AIConfig) -> BaseAIProvider:
        """Construct the Anthropic provider described by *config*.

        ``cli_bare`` is an Anthropic-only knob, so it is declared on
        :class:`~lintro.ai.providers.anthropic.config.AnthropicConfig` and read
        from the ``ai.providers.anthropic`` block here (#2309) rather than the
        factory assembling a per-vendor keyword list. Transport support is not
        re-validated: the provider constructor owns that rejection and its
        error text.

        Args:
            config: Effective AI configuration for this run.

        Returns:
            A configured
            :class:`~lintro.ai.providers.anthropic.provider.AnthropicProvider`.
        """
        from lintro.ai.providers.anthropic.provider import AnthropicProvider

        settings = anthropic_settings(config)
        return AnthropicProvider(
            model=config.model,
            api_key_env=config.api_key_env,
            max_tokens=config.max_tokens,
            base_url=config.api_base_url,
            transport=config.transport or AITransport.API,
            cli_bare=settings.cli_bare,
        )
