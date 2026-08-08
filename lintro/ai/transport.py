"""Transport resolution helpers for AI products (#1923)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from lintro.ai.enums import AITransport
from lintro.ai.enums.cost_basis import CostBasis

if TYPE_CHECKING:
    from lintro.ai.config import AIConfig

__all__ = [
    "DEFAULT_API_TIMEOUT",
    "DEFAULT_CLI_TIMEOUT",
    "ResolvedTransportSettings",
    "apply_resolved_transport",
    "apply_transport_override",
    "format_resolved_profile_log",
    "resolve_transport_settings",
]

DEFAULT_API_TIMEOUT = 60.0
DEFAULT_CLI_TIMEOUT = 900.0

AuthMode = Literal["api_key", "subscription", "unknown"]


@dataclass(frozen=True, slots=True)
class ResolvedTransportSettings:
    """Effective timeout/cost knobs for one transport.

    Attributes:
        transport: Active transport.
        timeout: Per-call / whole-turn timeout in seconds.
        max_cost_usd: Enforced or advisory cost ceiling (None = unlimited).
        cost_is_advisory: True when the cap cannot enforce spend (CLI/subscription).
        auth_mode: How the transport authenticates.
        cost_basis: How reported ``~$`` figures should be read.
    """

    transport: AITransport
    timeout: float
    max_cost_usd: float | None
    cost_is_advisory: bool
    auth_mode: AuthMode
    cost_basis: CostBasis


def apply_transport_override(
    ai_config: AIConfig,
    transport: str | AITransport | None,
) -> AIConfig:
    """Apply a CLI transport override on top of config.

    Args:
        ai_config: Loaded AI configuration.
        transport: Optional ``api`` or ``cli`` override from the CLI flag.

    Returns:
        Config unchanged when *transport* is ``None``, otherwise a copy
        with ``transport`` replaced.
    """
    if transport is None:
        return ai_config
    transport_enum = (
        transport
        if isinstance(transport, AITransport)
        else AITransport(str(transport).lower())
    )
    return ai_config.model_copy(update={"transport": transport_enum})


def resolve_transport_settings(ai_config: AIConfig) -> ResolvedTransportSettings:
    """Resolve timeout and cost for the config's active transport.

    Precedence: transport profile → legacy scalar → built-in default.

    Args:
        ai_config: AI configuration (transport may be None; defaults to api
            for resolution when unset — callers should still require transport
            for enabled AI features).

    Returns:
        Resolved settings for logging, budgets, and provider timeouts.
    """
    transport = ai_config.transport or AITransport.API
    profiles = ai_config.transports

    if transport is AITransport.CLI:
        # Legacy ``ai.api_timeout`` is API-sized (60s default) and must not
        # silently become the CLI whole-turn budget. CLI falls back to the
        # CLI built-in (900s) when the profile omits timeout (#1923).
        timeout = (
            profiles.cli.timeout
            if profiles.cli.timeout is not None
            else DEFAULT_CLI_TIMEOUT
        )
        max_cost = (
            profiles.cli.max_cost_usd_advisory
            if profiles.cli.max_cost_usd_advisory is not None
            else ai_config.max_cost_usd
        )
        return ResolvedTransportSettings(
            transport=transport,
            timeout=timeout,
            max_cost_usd=max_cost,
            cost_is_advisory=True,
            auth_mode="subscription",
            cost_basis=CostBasis.UNPRICEABLE,
        )

    timeout = (
        profiles.api.timeout
        if profiles.api.timeout is not None
        else ai_config.api_timeout
    )
    max_cost = (
        profiles.api.max_cost_usd
        if profiles.api.max_cost_usd is not None
        else ai_config.max_cost_usd
    )
    return ResolvedTransportSettings(
        transport=transport,
        timeout=timeout if timeout is not None else DEFAULT_API_TIMEOUT,
        max_cost_usd=max_cost,
        cost_is_advisory=False,
        auth_mode="api_key",
        cost_basis=CostBasis.BILLED,
    )


def apply_resolved_transport(ai_config: AIConfig) -> AIConfig:
    """Return a copy of *ai_config* with resolved timeout and cost applied.

    Writes the resolved values onto the legacy scalar fields so existing
    call sites that read ``api_timeout`` / ``max_cost_usd`` stay correct.

    Args:
        ai_config: AI configuration after transport override.

    Returns:
        Config copy with effective timeout and cost ceiling.
    """
    resolved = resolve_transport_settings(ai_config)
    return ai_config.model_copy(
        update={
            "api_timeout": resolved.timeout,
            "max_cost_usd": resolved.max_cost_usd,
        },
    )


def format_resolved_profile_log(settings: ResolvedTransportSettings) -> str:
    """Format a one-line log of the resolved transport profile.

    Args:
        settings: Resolved settings.

    Returns:
        Self-describing log line for CI and local runs.
    """
    if settings.max_cost_usd is None:
        cap = "none"
    elif settings.cost_is_advisory:
        cap = f"advisory:${settings.max_cost_usd:.2f}"
    else:
        cap = f"${settings.max_cost_usd:.2f}"
    return (
        f"transport={settings.transport.value} "
        f"auth={settings.auth_mode} "
        f"timeout={settings.timeout:g} "
        f"cap={cap} "
        f"cost_basis={settings.cost_basis.value}"
    )
