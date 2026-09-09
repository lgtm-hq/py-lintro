"""Cursor provider plugin.

Implements :class:`~lintro.ai.providers.protocol.ProviderPlugin` for Cursor.
:meth:`CursorPlugin.build` imports
:mod:`lintro.ai.providers.cursor.provider` on demand, so registration and
metadata lookups stay as cheap as the pre-migration lazy factory import.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from lintro.ai.enums import AITransport
from lintro.ai.provider_enum import AIProvider
from lintro.ai.providers.cursor.config import CursorConfig, cursor_settings
from lintro.ai.providers.cursor.metadata import CURSOR_METADATA

if TYPE_CHECKING:
    from lintro.ai.config import AIConfig
    from lintro.ai.providers.base import BaseAIProvider
    from lintro.ai.providers.protocol import ProviderMetadata

__all__ = ["CursorPlugin"]


@dataclass(frozen=True, slots=True)
class CursorPlugin:
    """Builds the Cursor provider from an effective :class:`AIConfig`."""

    @property
    def name(self) -> AIProvider:
        """Return the registry key for this plugin.

        Returns:
            :attr:`~lintro.ai.provider_enum.AIProvider.CURSOR`.
        """
        return AIProvider.CURSOR

    @property
    def transports(self) -> frozenset[AITransport]:
        """Return the transports Cursor serves.

        Declared once, on the metadata record, so the transports a plugin
        advertises and the ones doctor and config validation read cannot
        disagree (#2308).

        Returns:
            :attr:`CURSOR_METADATA.supported_transports`.
        """
        return CURSOR_METADATA.supported_transports

    @property
    def metadata(self) -> ProviderMetadata:
        """Return the static Cursor description.

        Returns:
            :data:`~lintro.ai.providers.cursor.metadata.CURSOR_METADATA`.
        """
        return CURSOR_METADATA

    @property
    def config_model(self) -> type[CursorConfig]:
        """Return the model for the ``ai.providers.cursor`` block.

        Returns:
            :class:`~lintro.ai.providers.cursor.config.CursorConfig`.
        """
        return CursorConfig

    def build(self, config: AIConfig) -> BaseAIProvider:
        """Construct the Cursor provider described by *config*.

        Workspace trust is a Cursor-only knob, so its single default lives on
        :class:`~lintro.ai.providers.cursor.config.CursorConfig` and reaches
        users as ``ai.providers.cursor.trust_workspace`` (#2309); the plugin
        forwards the resolved value here. An unset transport resolves to
        ``cli``, the only transport Cursor serves (#2449); an explicit
        ``transport: api`` is still rejected by the provider constructor with
        ``cursor provider only supports transport: cli``.

        Args:
            config: Effective AI configuration for this run.

        Returns:
            A configured :class:`~lintro.ai.providers.cursor.provider.CursorProvider`.
        """
        from lintro.ai.providers.cursor.provider import CursorProvider

        settings = cursor_settings(config)
        return CursorProvider(
            model=config.model,
            api_key_env=config.api_key_env,
            max_tokens=config.max_tokens,
            base_url=config.api_base_url,
            transport=config.transport or CURSOR_METADATA.default_transport,
            cursor_trust_workspace=settings.trust_workspace,
        )
