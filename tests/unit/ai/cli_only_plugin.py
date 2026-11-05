"""A fake CLI-only provider plugin for tests of transport-pairing rules.

Cursor was the in-tree CLI-only provider until #3253 gave it an API transport.
The doctor and factory rules for "a provider paired with a transport it does
not serve" still exist, so the tests that pinned them through Cursor now run
against this fake: it registers under Cursor's name with Cursor's metadata
minus the API transport, so the user-visible wording is the same as before.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from lintro.ai.enums import AITransport
from lintro.ai.exceptions import AINotAvailableError
from lintro.ai.provider_config import ProviderConfig
from lintro.ai.provider_enum import AIProvider
from lintro.ai.providers.cursor.metadata import CURSOR_METADATA
from lintro.ai.providers.registry import (
    all_providers,
    clear_registered,
    register_provider,
    restore_registered,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

    from lintro.ai.config import AIConfig
    from lintro.ai.providers.base import BaseAIProvider
    from lintro.ai.providers.protocol import ProviderMetadata

__all__ = ["CLI_ONLY_METADATA", "CliOnlyPlugin", "cli_only_plugin"]

#: Cursor's metadata as it stood before #3253: CLI transport only, no SDK.
CLI_ONLY_METADATA = replace(
    CURSOR_METADATA,
    supported_transports=frozenset({AITransport.CLI}),
    sdk_package=None,
    sdk_extra=None,
    accepts_api_base_url=True,
)


@dataclass(frozen=True, slots=True)
class CliOnlyPlugin:
    """Serves the CLI transport only and rejects every other at build time.

    Attributes:
        metadata: The CLI-only description.
    """

    metadata: ProviderMetadata = CLI_ONLY_METADATA

    @property
    def name(self) -> AIProvider:
        """Return the registry key this fake claims.

        Returns:
            The provider its metadata describes.
        """
        return self.metadata.provider

    @property
    def transports(self) -> frozenset[AITransport]:
        """Return the transports this fake serves.

        Returns:
            The CLI transport only.
        """
        return self.metadata.supported_transports

    @property
    def config_model(self) -> type[ProviderConfig]:
        """Return the model for this fake's ``ai.providers`` block.

        Returns:
            The base :class:`ProviderConfig`; the fake declares no settings.
        """
        return ProviderConfig

    def build(self, config: AIConfig) -> BaseAIProvider:
        """Reject any transport but CLI, the way a CLI-only provider must.

        Args:
            config: Effective AI configuration.

        Returns:
            Never returns; no test constructs a provider from this fake.

        Raises:
            AINotAvailableError: For a transport the fake does not serve.
            NotImplementedError: For the CLI transport.
        """
        if config.transport not in self.transports:
            raise AINotAvailableError(
                f"{self.name.value} provider only supports transport: cli",
            )
        raise NotImplementedError


@contextmanager
def cli_only_plugin() -> Iterator[CliOnlyPlugin]:
    """Run the block with the CLI-only fake as the only registered plugin.

    The AI provider registry is process-global and not covered by the
    lint-tool registry snapshot in ``tests/conftest.py``, so this restores
    the plugins it found on the way out.

    Yields:
        CliOnlyPlugin: The registered fake.
    """
    saved = all_providers()
    clear_registered()
    plugin = CliOnlyPlugin()
    try:
        register_provider(plugin)
        yield plugin
    finally:
        restore_registered(saved)
