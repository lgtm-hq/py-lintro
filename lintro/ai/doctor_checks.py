"""Transport-aware AI configuration checks for ``lintro doctor``."""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from enum import Enum

from lintro.ai.availability import (
    is_provider_available,
    provider_api_key_env,
    provider_cli_binary,
)
from lintro.ai.config import AIConfig
from lintro.ai.config_overrides import ENV_PROVIDER_BLOCK_PREFIX
from lintro.ai.enums import AITransport
from lintro.ai.liveness import LivenessState, check_liveness_sync
from lintro.ai.paths import resolve_workspace_root
from lintro.ai.provider_enum import (
    AIProvider,
    accepted_provider_values,
    provider_required_error,
)
from lintro.ai.providers.protocol import ProviderMetadata
from lintro.ai.registry import metadata_for
from lintro.ai.transcript import TRANSCRIPT_DIR, is_transcript_enabled
from lintro.enums.tool_status import ToolStatus

__all__ = ["AICheckResult", "check_ai_configuration", "check_ai_liveness"]

# Liveness states mapped onto the doctor status vocabulary. A depleted balance or
# a rejected key is a hard failure, not an "unknown": doctor exists to tell the
# operator that AI work will not run, and a soft status is how that gets missed.
_LIVENESS_STATUS: dict[LivenessState, ToolStatus] = {
    LivenessState.OK: ToolStatus.OK,
    LivenessState.MISSING_CREDENTIAL: ToolStatus.MISSING,
    LivenessState.AUTH_FAILED: ToolStatus.INCOMPATIBLE,
    LivenessState.NO_QUOTA: ToolStatus.INCOMPATIBLE,
    LivenessState.INCOMPATIBLE_CLI: ToolStatus.INCOMPATIBLE,
    # Transient: the credential itself may be fine, so do not brand it broken.
    LivenessState.RATE_LIMITED: ToolStatus.UNKNOWN,
    LivenessState.UNREACHABLE: ToolStatus.UNKNOWN,
    LivenessState.UNKNOWN: ToolStatus.UNKNOWN,
}


@dataclass(frozen=True)
class AICheckResult:
    """Result of a single AI configuration or dependency check."""

    name: str
    status: ToolStatus
    message: str
    hint: str = ""


def check_ai_liveness(config: AIConfig) -> list[AICheckResult]:
    """Probe the configured provider's credential and report it doctor-style.

    Opt-in rather than part of :func:`check_ai_configuration`: under API
    transport the probe is a real (one-token) call, and ``lintro doctor`` must not
    silently spend money or hit an external service on every invocation. It is
    what surfaces the one condition every presence check misses — a valid key with
    a depleted balance (#1826).

    Args:
        config: Parsed AI configuration.

    Returns:
        A single-element list describing the probe, or an empty list when no AI
        feature is enabled or provider/transport is unset.
    """
    if (
        not config.any_feature_enabled
        or config.transport is None
        or config.provider is None
    ):
        return []
    if not metadata_for(config.provider).supports(config.transport):
        # Structurally impossible pairing. check_ai_configuration already reports
        # it; probing anyway would fail on provider construction and surface a
        # misleading "no credential" verdict for what is a configuration error.
        return []

    result = check_liveness_sync(config=config)
    return [
        AICheckResult(
            name=f"ai.liveness.{result.provider}",
            status=_LIVENESS_STATUS.get(result.state, ToolStatus.UNKNOWN),
            message=result.message,
            hint=result.hint,
        ),
    ]


def check_ai_configuration(config: AIConfig) -> list[AICheckResult]:
    """Run transport-aware AI checks when AI features are enabled.

    Presence only -- SDK installed, binary on ``PATH``, key variable set. Whether
    the credential actually works is :func:`check_ai_liveness`, which is opt-in
    because probing costs a real call.

    Args:
        config: Parsed AI configuration.

    Returns:
        List of check results (empty when no AI feature -- ai.lint or
        ai.review -- is enabled, unless transcript logging is on).
    """
    results: list[AICheckResult] = []

    if is_transcript_enabled(config_enabled=config.transcript_logging):
        transcript_path = resolve_workspace_root(None) / TRANSCRIPT_DIR
        results.append(
            AICheckResult(
                name="ai.transcript",
                status=ToolStatus.OK,
                message=(
                    f"AI transcript logging enabled; writing NDJSON under "
                    f"{transcript_path}"
                ),
                hint=(
                    "Disable with ai.transcript_logging: false and unset "
                    "LINTRO_AI_TRANSCRIPT"
                ),
            ),
        )

    if not config.any_feature_enabled:
        return results

    if config.provider is None:
        results.append(
            AICheckResult(
                name="ai.provider",
                status=ToolStatus.INCOMPATIBLE,
                message=provider_required_error(),
                hint=(
                    "Set `ai.provider` in config, LINTRO_AI_PROVIDER, or "
                    f"--provider. Accepted: {accepted_provider_values()}"
                ),
            ),
        )

    if config.transport is None:
        results.append(
            AICheckResult(
                name="ai.transport",
                status=ToolStatus.INCOMPATIBLE,
                message=(
                    "ai.transport is required when ai.lint or ai.review is enabled"
                ),
                hint="Add `transport: api` or `transport: cli` under `ai:` in config",
            ),
        )

    if config.provider is None or config.transport is None:
        return results

    block_result = _check_provider_block(config=config)
    if block_result is not None:
        results.append(block_result)

    metadata = metadata_for(config.provider)
    if not metadata.supports(config.transport):
        only = metadata.default_transport.value
        results.append(
            AICheckResult(
                name="ai.provider+transport",
                status=ToolStatus.INCOMPATIBLE,
                message=(
                    f"{config.provider.value} provider only supports "
                    f"transport: {only}"
                ),
                hint=_pairing_hint(metadata=metadata),
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
                    f"Export {key_env} or set ai.api_base_url for a compatible endpoint"
                ),
            ),
        )

    return results


def _check_provider_block(*, config: AIConfig) -> AICheckResult | None:
    """Report the selected provider's ``ai.providers.<name>`` settings.

    Only the chosen provider's block is reported, and only when it differs
    from that provider's defaults: doctor exists to explain a surprising
    environment, and echoing every vendor's defaults back is noise (#2309).

    Args:
        config: Effective AI configuration, with a provider selected.

    Returns:
        A check naming the non-default settings, or None when the selected
        provider's block is entirely at its defaults.
    """
    provider = config.provider
    if provider is None:
        return None
    settings = config.provider_settings(provider)
    defaults = type(settings)()
    changed = {
        name: getattr(settings, name)
        for name in type(settings).model_fields
        if getattr(settings, name) != getattr(defaults, name)
    }
    if not changed:
        return None
    rendered = ", ".join(
        f"{name}={_render_setting(value)}" for name, value in sorted(changed.items())
    )
    return AICheckResult(
        name=f"ai.providers.{provider.value}",
        status=ToolStatus.OK,
        message=f"{provider.value} provider settings: {rendered}",
        hint=(
            f"Set under `ai.providers.{provider.value}` in config, "
            f"{ENV_PROVIDER_BLOCK_PREFIX}{provider.value.upper()}__<FIELD>, "
            f"or --provider-option"
        ),
    )


def _render_setting(value: object) -> str:
    """Render one provider-block value for a doctor line.

    Args:
        value: The effective setting value.

    Returns:
        The enum's own spelling for enums, ``str(value)`` otherwise, so a
        doctor line shows what a user would write in the config file.
    """
    return str(value.value) if isinstance(value, Enum) else str(value)


def _pairing_hint(*, metadata: ProviderMetadata) -> str:
    """Return the hint for a provider paired with a transport it cannot serve.

    Args:
        metadata: The configured provider's plugin metadata.

    Returns:
        Guidance naming the transport to set, and the CLI to install when that
        transport is the CLI one.
    """
    only = metadata.default_transport
    hint = f"Set `transport: {only.value}`"
    if only is AITransport.CLI:
        return f"{hint} and install the {metadata.display_name} agent CLI"
    return hint


def _cli_install_hint(*, provider: AIProvider) -> str:
    """Return the install guidance for a provider's missing CLI binary.

    Args:
        provider: The provider whose binary was not found on ``PATH``.

    Returns:
        The hint the provider's plugin metadata declares, or a generic one when
        it declares none.
    """
    return (
        metadata_for(provider).cli_install_hint
        or "Install the provider CLI and ensure it is on PATH"
    )


def _check_cli_auth(
    *,
    provider: AIProvider,
    config: AIConfig,
) -> AICheckResult | None:
    """Report whether the provider's CLI is likely to authenticate.

    Presence only, and never spawns the binary: the probe each provider's
    metadata declares says which environment variables and login files count as
    proof. An unproven credential is ``UNKNOWN`` rather than a failure, because
    an interactive vendor login lintro cannot see is the common case.

    Args:
        provider: The provider whose CLI transport is configured.
        config: Parsed AI configuration, consulted for an ``api_key_env``
            override.

    Returns:
        The check result, or None when the provider declares no auth probe.
    """
    probe = metadata_for(provider).cli_auth_probe
    if probe is None:
        return None
    # Resolved only for the probes that read it. A provider whose CLI reads a
    # different variable than its SDK (OpenAI: `codex` reads CODEX_API_KEY,
    # never OPENAI_API_KEY) declares ``honors_api_key_env=False`` and is never
    # handed the API-transport variable, so neither the provider default nor an
    # `ai.api_key_env` override can sway its CLI verdict. Written as a statement
    # rather than a conditional expression: the one-liner parsed correctly, but
    # two readers in a row misread `a or b if c else d`, and this is a
    # credential check.
    key_env = ""
    if probe.honors_api_key_env:
        key_env = config.api_key_env or provider_api_key_env(provider)
    if probe.is_configured(key_env=key_env):
        return AICheckResult(
            name="ai.cli.auth",
            status=ToolStatus.OK,
            message=probe.describe(key_env=key_env),
        )
    return AICheckResult(
        name="ai.cli.auth",
        status=ToolStatus.UNKNOWN,
        message=probe.unverified_message,
        hint=probe.hint,
    )
