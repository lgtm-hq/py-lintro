"""Static description of the Cursor provider.

Cursor serves two transports. ``cli`` (the default, #2449) runs the ``agent``
binary once per call. ``api`` (#3253) runs one inference-only prompt through
Cursor's first-party Python SDK, ``cursor-sdk``, which ships in its own opt-in
``lintro[cursor]`` extra rather than ``lintro[ai]``: it is wheels-only with a
~55 MB native bridge per wheel, and a public beta. ``sdk_package`` is the
import name ``cursor_sdk`` because :mod:`lintro.ai.availability` imports it.
The SDK has no endpoint override, so ``ai.api_base_url`` is not accepted.
Everything here is answerable without importing the SDK or spawning a binary.

Since #2308 this module *is* the declaration: pricing, defaults, the API-key
variable, the CLI binary and its contract, the doctor install hint and the auth
probe are stated once here and read everywhere else through
:mod:`lintro.ai.registry`'s facade. The runtime ``CliTransport`` install hint in
:mod:`lintro.ai.providers.cursor.provider` is still a separate string; unifying
the two would change a user-visible message, so it is left to #2449.
"""

from __future__ import annotations

from lintro.ai.enums import AITransport
from lintro.ai.model_pricing import ModelPricing
from lintro.ai.provider_enum import AIProvider
from lintro.ai.providers.cli_auth_probe import CliAuthProbe
from lintro.ai.providers.cursor.cli_contract import CURSOR_CLI_CONTRACT
from lintro.ai.providers.protocol import CliBounds, ProviderMetadata

__all__ = ["CURSOR_CLI_BINARY", "CURSOR_METADATA"]

#: Executable looked up on ``PATH`` for Cursor CLI transport.
CURSOR_CLI_BINARY = "agent"

#: Static description of the Cursor provider.
#:
#: Cursor bills against a subscription rather than per token, on both
#: transports, so its models carry zero rates.
#: :func:`lintro.ai.cost.estimate_cost_with_floor` is what keeps
#: ``ai.max_cost_usd`` meaningful for them.
CURSOR_METADATA = ProviderMetadata(
    provider=AIProvider.CURSOR,
    display_name="Cursor",
    default_model="auto",
    default_api_key_env="CURSOR_API_KEY",
    supported_transports=frozenset({AITransport.API, AITransport.CLI}),
    # The CLI stays the documented default (#2449); `transport: api` is opt-in.
    default_transport=AITransport.CLI,
    sdk_package="cursor_sdk",
    sdk_extra="cursor",
    accepts_api_base_url=False,
    cli_binary=CURSOR_CLI_BINARY,
    cli_contract_id=AIProvider.CURSOR.value,
    cli_contract=CURSOR_CLI_CONTRACT,
    # `--mode ask` is the read-only mode; no per-call turn limit (#2685).
    cli_bounds=CliBounds(read_only_args=("--mode", "ask")),
    cli_install_hint="Install agent CLI: curl https://cursor.com/install -fsS | bash",
    cli_auth_probe=CliAuthProbe(
        # The `agent` CLI reads CURSOR_API_KEY itself.
        honors_api_key_env=True,
        configured_message="{key_env} is set",
        unverified_message="Cursor CLI auth not verified",
        hint="Run `agent login` or set CURSOR_API_KEY",
    ),
    pricing={
        "auto": ModelPricing(0.0, 0.0),
        "gpt-5.3-codex-fast": ModelPricing(0.0, 0.0),
    },
)
