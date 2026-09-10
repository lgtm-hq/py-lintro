"""Static description of the Anthropic provider.

Everything here is answerable without importing the ``anthropic`` SDK or
spawning the ``claude`` binary, so :mod:`lintro.ai.providers.anthropic` stays a
cheap import and only :mod:`lintro.ai.providers.anthropic.provider` pulls the
vendor SDK.

Since #2308 this module *is* the declaration: pricing, defaults, the API-key
variable, the CLI binary and its contract, the install hint and the auth probe
are stated once here and read everywhere else through
:mod:`lintro.ai.registry`'s facade.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import MappingProxyType

from lintro.ai.enums import AITransport
from lintro.ai.model_pricing import ModelPricing
from lintro.ai.provider_enum import AIProvider
from lintro.ai.providers.anthropic.cli_contract import ANTHROPIC_CLI_CONTRACT
from lintro.ai.providers.cli_auth_probe import CliAuthProbe
from lintro.ai.providers.protocol import ProviderMetadata

__all__ = [
    "ANTHROPIC_CLI_BINARY",
    "ANTHROPIC_MANAGED_SETTINGS_ENV",
    "ANTHROPIC_MANAGED_SETTINGS_PATHS",
    "ANTHROPIC_METADATA",
    "managed_settings_path",
]

#: Executable looked up on ``PATH`` for Anthropic CLI transport.
ANTHROPIC_CLI_BINARY = "claude"

#: Environment variable that relocates the enterprise-managed Claude Code
#: settings file. The documented per-platform locations below are absolute and
#: outside any workspace, so a sandbox, a test, or a distro that packages
#: Claude Code elsewhere has no other way to point lintro at the real file.
ANTHROPIC_MANAGED_SETTINGS_ENV = "LINTRO_CLAUDE_MANAGED_SETTINGS"

#: Claude Code's documented enterprise-managed settings file, per platform.
#: Keyed by the ``sys.platform`` prefix it applies to. A platform absent from
#: this table has no documented location, and lintro looks for none.
ANTHROPIC_MANAGED_SETTINGS_PATHS = MappingProxyType(
    {
        "darwin": "/Library/Application Support/ClaudeCode/managed-settings.json",
        "win32": "C:/ProgramData/ClaudeCode/managed-settings.json",
        "linux": "/etc/claude-code/managed-settings.json",
    },
)


def managed_settings_path(*, override: str | None = None) -> Path | None:
    """Return the enterprise-managed Claude settings file for this platform.

    Args:
        override: Value of :data:`ANTHROPIC_MANAGED_SETTINGS_ENV`, or ``None``
            when it is unset. A blank value is treated as unset so an empty
            export does not silently disable the lookup.

    Returns:
        The managed settings path, or ``None`` when the platform has no
        documented location.
    """
    if override is not None and override.strip():
        return Path(override.strip())
    for prefix, path in ANTHROPIC_MANAGED_SETTINGS_PATHS.items():
        if sys.platform.startswith(prefix):
            return Path(path)
    return None


#: Static description of the Anthropic provider.
ANTHROPIC_METADATA = ProviderMetadata(
    provider=AIProvider.ANTHROPIC,
    display_name="Anthropic",
    default_model="claude-sonnet-4-6",
    default_api_key_env="ANTHROPIC_API_KEY",
    supported_transports=frozenset({AITransport.API, AITransport.CLI}),
    default_transport=AITransport.API,
    sdk_package="anthropic",
    cli_binary=ANTHROPIC_CLI_BINARY,
    cli_contract_id=AIProvider.ANTHROPIC.value,
    cli_contract=ANTHROPIC_CLI_CONTRACT,
    cli_install_hint="Install Claude Code: https://code.claude.com/docs/en/setup",
    cli_auth_probe=CliAuthProbe(
        # Pre-#2308 behaviour, preserved verbatim: doctor accepts whichever
        # variable `ai.api_key_env` resolves to. The `claude` binary itself
        # only ever reads ANTHROPIC_API_KEY (see
        # `claude_auth.CLAUDE_API_KEY_ENV`), so a renamed variable makes this
        # probe report OK for a credential the CLI cannot see. That mismatch
        # predates this refactor and is left for #2449 rather than changed
        # here; the OpenAI probe shows the shape the fix would take.
        honors_api_key_env=True,
        configured_message="{key_env} set (API billing overrides subscription)",
        unverified_message="Claude CLI auth not verified",
        hint="Run `claude login` or set ANTHROPIC_API_KEY",
    ),
    pricing={
        "claude-sonnet-4-6": ModelPricing(3.00, 15.00),
        "claude-sonnet-4-20250514": ModelPricing(3.00, 15.00),
        "claude-haiku-4-5-20251001": ModelPricing(0.80, 4.00),
        "claude-opus-4-20250514": ModelPricing(15.00, 75.00),
    },
)
