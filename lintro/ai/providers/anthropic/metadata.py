"""Static description of the Anthropic provider.

Everything here is answerable without importing the ``anthropic`` SDK or
spawning the ``claude`` binary, so :mod:`lintro.ai.providers.anthropic` stays a
cheap import and only :mod:`lintro.ai.providers.anthropic.provider` pulls the
vendor SDK.

The values are read from the existing tables rather than copied: folding
:data:`lintro.ai.registry.PROVIDERS` and friends into this record is phase 3
(#2308), so today this module is a view over them, not a second source of
truth.
"""

from __future__ import annotations

from lintro.ai.provider_enum import AIProvider
from lintro.ai.providers.protocol import ProviderMetadata
from lintro.ai.registry import PROVIDERS

__all__ = ["ANTHROPIC_CLI_BINARY", "ANTHROPIC_METADATA"]

#: Executable looked up on ``PATH`` for Anthropic CLI transport.
ANTHROPIC_CLI_BINARY = "claude"

#: Static description of the Anthropic provider.
ANTHROPIC_METADATA = ProviderMetadata(
    provider=AIProvider.ANTHROPIC,
    default_model=PROVIDERS.anthropic.default_model,
    default_api_key_env=PROVIDERS.anthropic.default_api_key_env,
    sdk_package="anthropic",
    cli_binary=ANTHROPIC_CLI_BINARY,
    cli_contract_id=AIProvider.ANTHROPIC.value,
    pricing=PROVIDERS.anthropic.models,
)
