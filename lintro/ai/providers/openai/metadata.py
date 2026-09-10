"""Static description of the OpenAI provider.

Everything here is answerable without importing the ``openai`` SDK or spawning
the ``codex`` binary, so :mod:`lintro.ai.providers.openai` stays a cheap import
and only :mod:`lintro.ai.providers.openai.provider` pulls the vendor SDK.

Since #2308 this module *is* the declaration: pricing, defaults, the API-key
variable, the CLI binary and its contract, the install hint and the auth probe
are stated once here and read everywhere else through
:mod:`lintro.ai.registry`'s facade.
"""

from __future__ import annotations

from lintro.ai.enums import AITransport
from lintro.ai.model_pricing import ModelPricing
from lintro.ai.provider_enum import AIProvider
from lintro.ai.providers.cli_auth_probe import CliAuthProbe
from lintro.ai.providers.openai.cli_contract import OPENAI_CLI_CONTRACT
from lintro.ai.providers.protocol import ProviderMetadata

__all__ = ["OPENAI_CLI_BINARY", "OPENAI_METADATA"]

#: Executable looked up on ``PATH`` for OpenAI CLI transport.
OPENAI_CLI_BINARY = "codex"

#: Static description of the OpenAI provider.
OPENAI_METADATA = ProviderMetadata(
    provider=AIProvider.OPENAI,
    display_name="OpenAI",
    default_model="gpt-4o",
    default_api_key_env="OPENAI_API_KEY",
    supported_transports=frozenset({AITransport.API, AITransport.CLI}),
    default_transport=AITransport.API,
    sdk_package="openai",
    cli_binary=OPENAI_CLI_BINARY,
    cli_contract_id=AIProvider.OPENAI.value,
    cli_contract=OPENAI_CLI_CONTRACT,
    cli_install_hint="Install Codex CLI: https://developers.openai.com/codex/cli",
    cli_auth_probe=CliAuthProbe(
        # `codex` reads its own CODEX_API_KEY and its login writes
        # ~/.codex/auth.json; it never looks at OPENAI_API_KEY, so an API-key
        # variable set for the SDK transport proves nothing about the CLI.
        honors_api_key_env=False,
        extra_env_vars=("CODEX_API_KEY",),
        auth_files=(".codex/auth.json",),
        configured_message=(
            "Codex auth configured (CODEX_API_KEY or ~/.codex/auth.json)"
        ),
        unverified_message="Codex CLI auth not verified",
        hint="Run `codex login` or set CODEX_API_KEY",
    ),
    pricing={
        "gpt-4o": ModelPricing(2.50, 10.00),
        "gpt-4o-mini": ModelPricing(0.15, 0.60),
        "gpt-4-turbo": ModelPricing(10.00, 30.00),
        "o1": ModelPricing(15.00, 60.00),
        "o1-mini": ModelPricing(1.10, 4.40),
    },
)
