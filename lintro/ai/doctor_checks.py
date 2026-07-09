"""Transport-aware AI configuration checks for ``lintro doctor``."""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass

from lintro.ai.availability import (
    codex_auth_configured,
    is_provider_available,
    provider_api_key_env,
    provider_cli_binary,
)
from lintro.ai.config import AIConfig
from lintro.ai.enums import AITransport
from lintro.ai.registry import AIProvider
from lintro.enums.tool_status import ToolStatus

__all__ = ["AICheckResult", "check_ai_configuration"]


@dataclass(frozen=True)
class AICheckResult:
    """Result of a single AI configuration or dependency check."""

    name: str
    status: ToolStatus
    message: str
    hint: str = ""


def check_ai_configuration(config: AIConfig) -> list[AICheckResult]:
    """Run transport-aware AI checks when AI features are enabled.

    Args:
        config: Parsed AI configuration.

    Returns:
        List of check results (empty when ``ai.enabled`` is false).
    """
    if not config.enabled:
        return []

    results: list[AICheckResult] = []

    if config.transport is None:
        results.append(
            AICheckResult(
                name="ai.transport",
                status=ToolStatus.INCOMPATIBLE,
                message="ai.transport is required when ai.enabled is true",
                hint="Add `transport: api` or `transport: cli` under `ai:` in config",
            ),
        )
        return results

    if config.provider == AIProvider.CURSOR and config.transport == AITransport.API:
        results.append(
            AICheckResult(
                name="ai.provider+transport",
                status=ToolStatus.INCOMPATIBLE,
                message="cursor provider only supports transport: cli",
                hint="Set `transport: cli` and install the Cursor agent CLI",
            ),
        )
        return results

    transport = config.transport
    provider = config.provider

    if transport == AITransport.CLI:
        binary = provider_cli_binary(provider)
        if binary is None:
            results.append(
                AICheckResult(
                    name="ai.cli",
                    status=ToolStatus.INCOMPATIBLE,
                    message=f"No CLI transport for provider {provider.value}",
                    hint="Use `transport: api` or choose a different provider",
                ),
            )
            return results

        path = shutil.which(binary)
        if path is None:
            hint = _cli_install_hint(provider=provider)
            results.append(
                AICheckResult(
                    name=f"ai.cli.{binary}",
                    status=ToolStatus.MISSING,
                    message=f"CLI binary '{binary}' not found on PATH",
                    hint=hint,
                ),
            )
        else:
            results.append(
                AICheckResult(
                    name=f"ai.cli.{binary}",
                    status=ToolStatus.OK,
                    message=f"CLI binary '{binary}' found at {path}",
                ),
            )

        auth_result = _check_cli_auth(provider=provider, config=config)
        if auth_result is not None:
            results.append(auth_result)
        return results

    # API transport
    if not is_provider_available(provider, transport=AITransport.API):
        results.append(
            AICheckResult(
                name=f"ai.api.sdk.{provider.value}",
                status=ToolStatus.MISSING,
                message=(
                    f"Provider SDK for {provider.value} API transport is not installed"
                ),
                hint="Install with: uv pip install 'lintro[ai]'",
            ),
        )
        return results

    key_env = config.api_key_env or provider_api_key_env(provider)
    if config.api_base_url or os.environ.get(key_env):
        results.append(
            AICheckResult(
                name=f"ai.api.{key_env}",
                status=ToolStatus.OK,
                message=f"API credentials configured via {key_env} or api_base_url",
            ),
        )
    else:
        results.append(
            AICheckResult(
                name=f"ai.api.{key_env}",
                status=ToolStatus.MISSING,
                message=f"Environment variable {key_env} is not set",
                hint=(
                    f"Export {key_env} or set ai.api_base_url "
                    "for a compatible endpoint"
                ),
            ),
        )

    return results


def _cli_install_hint(*, provider: AIProvider) -> str:
    if provider == AIProvider.CURSOR:
        return "Install agent CLI: curl https://cursor.com/install -fsS | bash"
    if provider == AIProvider.ANTHROPIC:
        return "Install Claude Code: https://code.claude.com/docs/en/setup"
    if provider == AIProvider.OPENAI:
        return "Install Codex CLI: https://developers.openai.com/codex/cli"
    return "Install the provider CLI and ensure it is on PATH"


def _check_cli_auth(
    *,
    provider: AIProvider,
    config: AIConfig,
) -> AICheckResult | None:
    if provider == AIProvider.CURSOR:
        key_env = config.api_key_env or provider_api_key_env(provider)
        if os.environ.get(key_env):
            return AICheckResult(
                name="ai.cli.auth",
                status=ToolStatus.OK,
                message=f"{key_env} is set",
            )
        return AICheckResult(
            name="ai.cli.auth",
            status=ToolStatus.UNKNOWN,
            message="Cursor CLI auth not verified",
            hint="Run `agent login` or set CURSOR_API_KEY",
        )

    if provider == AIProvider.ANTHROPIC:
        key_env = config.api_key_env or provider_api_key_env(provider)
        if os.environ.get(key_env):
            return AICheckResult(
                name="ai.cli.auth",
                status=ToolStatus.OK,
                message=f"{key_env} set (API billing overrides subscription)",
            )
        return AICheckResult(
            name="ai.cli.auth",
            status=ToolStatus.UNKNOWN,
            message="Claude CLI auth not verified",
            hint="Run `claude login` or set ANTHROPIC_API_KEY",
        )

    if provider == AIProvider.OPENAI:
        if codex_auth_configured():
            return AICheckResult(
                name="ai.cli.auth",
                status=ToolStatus.OK,
                message="Codex auth configured (CODEX_API_KEY or ~/.codex/auth.json)",
            )
        return AICheckResult(
            name="ai.cli.auth",
            status=ToolStatus.UNKNOWN,
            message="Codex CLI auth not verified",
            hint="Run `codex login` or set CODEX_API_KEY",
        )

    return None
