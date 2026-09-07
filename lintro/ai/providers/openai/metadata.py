"""Static description of the OpenAI provider.

Everything here is answerable without importing the ``openai`` SDK or spawning
the ``codex`` binary, so :mod:`lintro.ai.providers.openai` stays a cheap import
and only :mod:`lintro.ai.providers.openai.provider` pulls the vendor SDK.

The values are read from the existing tables rather than copied: folding
:data:`lintro.ai.registry.PROVIDERS` and friends into this record is phase 3
(#2308), so today this module is a view over them, not a second source of
truth.
"""

from __future__ import annotations

from lintro.ai.provider_enum import AIProvider
from lintro.ai.providers.protocol import ProviderMetadata
from lintro.ai.registry import PROVIDERS

__all__ = ["OPENAI_CLI_BINARY", "OPENAI_METADATA"]

#: Executable looked up on ``PATH`` for OpenAI CLI transport.
OPENAI_CLI_BINARY = "codex"

#: Static description of the OpenAI provider.
OPENAI_METADATA = ProviderMetadata(
    provider=AIProvider.OPENAI,
    default_model=PROVIDERS.openai.default_model,
    default_api_key_env=PROVIDERS.openai.default_api_key_env,
    sdk_package="openai",
    cli_binary=OPENAI_CLI_BINARY,
    cli_contract_id=AIProvider.OPENAI.value,
    pricing=PROVIDERS.openai.models,
)
