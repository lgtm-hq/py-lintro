"""Static description of the Cursor provider.

Cursor has no API transport: the CreateAgent HTTP API is not used (see
:mod:`lintro.ai.providers.cursor.provider`), so ``sdk_package`` is ``None`` and
the ``agent`` CLI is the only backend. Everything here is answerable without
spawning that binary.

The values are read from the existing tables rather than copied: folding
:data:`lintro.ai.registry.PROVIDERS` and friends into this record is phase 3
(#2308), so today this module is a view over them, not a second source of
truth.
"""

from __future__ import annotations

from lintro.ai.provider_enum import AIProvider
from lintro.ai.providers.protocol import ProviderMetadata
from lintro.ai.registry import PROVIDERS

__all__ = ["CURSOR_CLI_BINARY", "CURSOR_METADATA"]

#: Executable looked up on ``PATH`` for Cursor CLI transport.
CURSOR_CLI_BINARY = "agent"

#: Static description of the Cursor provider.
CURSOR_METADATA = ProviderMetadata(
    provider=AIProvider.CURSOR,
    default_model=PROVIDERS.cursor.default_model,
    default_api_key_env=PROVIDERS.cursor.default_api_key_env,
    sdk_package=None,
    cli_binary=CURSOR_CLI_BINARY,
    cli_contract_id=AIProvider.CURSOR.value,
    pricing=PROVIDERS.cursor.models,
)
