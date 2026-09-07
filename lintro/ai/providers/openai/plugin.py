"""OpenAI provider plugin.

Implements :class:`~lintro.ai.providers.protocol.ProviderPlugin` for OpenAI.
The plugin itself never imports the ``openai`` SDK: :meth:`OpenAIPlugin.build`
imports :mod:`lintro.ai.providers.openai.provider` on demand, so registration
and metadata lookups stay as cheap as the pre-migration lazy factory import.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from lintro.ai.enums import AITransport
from lintro.ai.provider_enum import AIProvider
from lintro.ai.providers.openai.metadata import OPENAI_METADATA

if TYPE_CHECKING:
    from lintro.ai.config import AIConfig
    from lintro.ai.providers.base import BaseAIProvider
    from lintro.ai.providers.protocol import ProviderMetadata

__all__ = ["OpenAIPlugin"]


@dataclass(frozen=True, slots=True)
class OpenAIPlugin:
    """Builds the OpenAI provider from an effective :class:`AIConfig`."""

    @property
    def name(self) -> AIProvider:
        """Return the registry key for this plugin.

        Returns:
            :attr:`~lintro.ai.provider_enum.AIProvider.OPENAI`.
        """
        return AIProvider.OPENAI

    @property
    def transports(self) -> frozenset[AITransport]:
        """Return the transports OpenAI serves.

        Returns:
            Both ``api`` (the ``openai`` SDK) and ``cli`` (``codex exec``).
        """
        return frozenset({AITransport.API, AITransport.CLI})

    @property
    def metadata(self) -> ProviderMetadata:
        """Return the static OpenAI description.

        Returns:
            :data:`~lintro.ai.providers.openai.metadata.OPENAI_METADATA`.
        """
        return OPENAI_METADATA

    def build(self, config: AIConfig) -> BaseAIProvider:
        """Construct the OpenAI provider described by *config*.

        OpenAI takes no vendor-specific knobs today, so this reads only the
        shared transport and budget fields.

        Args:
            config: Effective AI configuration for this run.

        Returns:
            A configured :class:`~lintro.ai.providers.openai.provider.OpenAIProvider`.
        """
        from lintro.ai.providers.openai.provider import OpenAIProvider

        return OpenAIProvider(
            model=config.model,
            api_key_env=config.api_key_env,
            max_tokens=config.max_tokens,
            base_url=config.api_base_url,
            transport=config.transport or AITransport.API,
        )
