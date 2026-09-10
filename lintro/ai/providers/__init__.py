"""AI provider factory.

Provides the ``get_provider()`` factory function that resolves the configured
provider through the in-tree plugin registry
(:mod:`lintro.ai.providers.registry`) and asks the registered plugin to build
it. Adding a vendor means adding an ``AIProvider`` member and a package under
``lintro/ai/providers/``; this module is never edited for it. See
``docs/adr/0009-ai-provider-plugin-contract.md``.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from lintro.ai.exceptions import (
    AINotAvailableError,
    AIProviderNotRegisteredError,
    AIProviderRequiredError,
)
from lintro.ai.paths import resolve_workspace_root
from lintro.ai.provider_enum import (
    AIProvider,
    accepted_provider_values,
    provider_required_error,
)
from lintro.ai.providers.builtins import load_builtin_providers
from lintro.ai.providers.registry import all_providers, get_registered
from lintro.ai.registry import metadata_for
from lintro.ai.transcript import maybe_start_transcript

if TYPE_CHECKING:
    from lintro.ai.config import AIConfig
    from lintro.ai.providers.base import BaseAIProvider


def get_provider(
    config: AIConfig,
    *,
    workspace_root: Path | None = None,
    transcript_command: str | None = None,
) -> BaseAIProvider:
    """Instantiate an AI provider from configuration.

    Resolution is registry-driven: the provider name is validated against
    :class:`~lintro.ai.provider_enum.AIProvider`, the in-tree plugins are
    loaded, and the plugin registered under that name builds the provider from
    *config*. Provider-specific knobs live on that provider's own
    ``ai.providers.<name>`` block (#2309) and are read by the plugin that owns
    them, so no per-vendor keyword list is assembled here.

    Args:
        config: AI configuration specifying provider, model, and API key.
        workspace_root: Optional workspace root for transcript cache paths.
            Defaults to the current working directory.
        transcript_command: Optional command label for the transcript
            filename. Callers that know the verb they run under state it here;
            everything else falls back to ``DEFAULT_COMMAND_LABEL``. lintro
            never infers the verb from ``sys.argv`` (#1998).

    Returns:
        BaseAIProvider: Configured provider instance.

    Raises:
        AIProviderRequiredError: If no provider is set. The message names
            ``ai.provider``, ``LINTRO_AI_PROVIDER``, and ``--provider``.
        ValueError: If the provider name is not recognized, or is recognized
            but has no registered plugin.
    """
    if config.provider is None:
        raise AIProviderRequiredError(provider_required_error())
    try:
        provider_enum = AIProvider(str(config.provider).lower())
    except ValueError as exc:
        raise ValueError(
            f"Unknown AI provider: '{config.provider}'. "
            f"Supported providers: {accepted_provider_values()}",
        ) from exc

    load_builtin_providers()
    try:
        plugin = get_registered(provider_enum)
    except AIProviderNotRegisteredError as exc:
        implemented = ", ".join(p.value for p in all_providers())
        raise ValueError(
            f"AI provider '{provider_enum.value}' is recognized but not "
            f"implemented. Implemented providers: {implemented}",
        ) from exc

    maybe_start_transcript(
        workspace_root=workspace_root or resolve_workspace_root(None),
        config_enabled=config.transcript_logging,
        retention=config.transcript_retention,
        command=transcript_command,
    )

    return plugin.build(config)


def get_default_model(provider_name: str) -> str | None:
    """Get the default model for a provider without importing its SDK.

    Args:
        provider_name: Provider name (e.g. "anthropic", "openai").

    Returns:
        Default model identifier, or None if provider is unknown.
    """
    try:
        return metadata_for(provider_name).default_model
    except AIProviderNotRegisteredError:
        return None


__all__ = ["AINotAvailableError", "get_default_model", "get_provider"]
