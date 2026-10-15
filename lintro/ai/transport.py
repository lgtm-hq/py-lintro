"""Transport resolution helpers for AI products (#1923)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from lintro.ai.enums import AITransport, ConfigSource
from lintro.ai.enums.cost_basis import CostBasis
from lintro.ai.enums.credential_kind import CredentialKind
from lintro.ai.provenance import auth_mode_for, detect_run_provenance
from lintro.ai.providers.anthropic.config import anthropic_settings
from lintro.ai.providers.claude_auth import should_send_bare
from lintro.ai.registry import AIProvider
from lintro.ai.resolved_ai_config import ResolvedAIConfig

if TYPE_CHECKING:
    from lintro.ai.config import AIConfig

__all__ = [
    "DEFAULT_API_TIMEOUT",
    "DEFAULT_CLI_TIMEOUT",
    "ResolvedTransportSettings",
    "apply_resolved_transport",
    "format_resolved_profile_log",
    "resolve_cost_basis",
    "resolve_max_cost_with_source",
    "resolve_transport_settings",
]

DEFAULT_API_TIMEOUT = 60.0
DEFAULT_CLI_TIMEOUT = 1800.0

AuthMode = Literal["api_key", "subscription", "gateway_token", "unknown"]


@dataclass(frozen=True, slots=True)
class ResolvedTransportSettings:
    """Effective timeout/cost knobs for one transport.

    Attributes:
        transport: Active transport.
        timeout: Per-call / per-chunk timeout in seconds.
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


def resolve_cost_basis(*, auth_mode: str, estimated: bool) -> CostBasis | None:
    """Resolve cost provenance for a recorded run.

    Args:
        auth_mode: Authentication mode used for the run (``api_key`` /
            ``subscription`` / empty when unknown).
        estimated: Whether token counts were priced locally.

    Returns:
        ``unpriceable`` under subscription billing; ``estimated`` or
        ``billed`` for metered API-key paths; ``None`` when provenance
        cannot be inferred (legacy blobs with neither auth nor estimate).
    """
    normalized = (auth_mode or "").strip().lower().replace("-", "_")
    if normalized == "subscription":
        return CostBasis.UNPRICEABLE
    if normalized == "api_key":
        return CostBasis.ESTIMATED if estimated else CostBasis.BILLED
    if estimated:
        return CostBasis.ESTIMATED
    return None


def _auth_mode(*, credential: CredentialKind, bare_billing: bool) -> AuthMode:
    """Return the auth mode the credential implies, or the policy's label.

    Args:
        credential: The credential kind present in the environment.
        bare_billing: Whether the CLI bills an API key under ``--bare``.

    Returns:
        ``gateway_token`` for a gateway token, otherwise ``api_key`` or
        ``subscription`` as the credential or, failing that, the policy says.
    """
    derived = auth_mode_for(credential)
    if derived == "gateway_token":
        return "gateway_token"
    if derived == "api_key" or (not derived and bare_billing):
        return "api_key"
    return "subscription"


def cli_bare_billing(ai_config: AIConfig) -> bool:
    """Return whether the ``claude`` CLI will be sent ``--bare`` for *ai_config*.

    Under ``--bare`` the call bills the API key, not the subscription; the
    provenance, auth mode and cost basis of a CLI run all follow from it.

    Args:
        ai_config: AI configuration.

    Returns:
        True for the Anthropic provider when its bare-mode policy resolves to
        sending the flag; False for every other provider.
    """
    return ai_config.provider is AIProvider.ANTHROPIC and should_send_bare(
        configured=anthropic_settings(ai_config).cli_bare,
    )


def _profile_max_cost(ai_config: AIConfig) -> float | None:
    """Return the active transport profile's cost cap, if the profile sets one.

    Single source for the profile-cap rule shared by
    :func:`resolve_transport_settings` and
    :func:`resolve_max_cost_with_source`: the CLI profile carries an
    advisory cap, the API profile an enforced one.

    Args:
        ai_config: AI configuration (transport may be None; defaults to api).

    Returns:
        The profile cap, or None when the active profile omits it.
    """
    transport = ai_config.transport or AITransport.API
    profiles = ai_config.transports
    if transport is AITransport.CLI:
        return profiles.cli.max_cost_usd_advisory
    return profiles.api.max_cost_usd


def resolve_transport_settings(
    ai_config: AIConfig,
    *,
    cwd: str | None = None,
) -> ResolvedTransportSettings:
    """Resolve timeout and cost for the config's active transport.

    Precedence: transport profile → legacy scalar → built-in default.

    The auth mode and cost basis read the credential and endpoint the run
    will use (#2867), which means the Claude settings files and the Codex
    session file on disk: a dry run and CI can therefore resolve differently
    when their disks differ, and the profile log says which was found.

    Args:
        ai_config: AI configuration (transport may be None; defaults to api
            for resolution when unset — callers should still require transport
            for enabled AI features).
        cwd: Working directory the CLI subprocess runs in, so project-level
            settings files are read from the same place the run reads them.

    Returns:
        Resolved settings for logging, budgets, and provider timeouts.
    """
    transport = ai_config.transport or AITransport.API
    profiles = ai_config.transports

    if transport is AITransport.CLI:
        # Legacy ``ai.api_timeout`` is API-sized (60s default) and must not
        # silently become the CLI per-chunk budget. CLI falls back to the
        # CLI built-in (1800s) when the profile omits timeout (#1923).
        timeout = (
            profiles.cli.timeout
            if profiles.cli.timeout is not None
            else DEFAULT_CLI_TIMEOUT
        )
        profile_cap = _profile_max_cost(ai_config)
        max_cost = profile_cap if profile_cap is not None else ai_config.max_cost_usd
        # Anthropic's CLI switches to --bare when an API key is reachable
        # (CliBareMode.AUTO, #1859): the call then bills the key, not the
        # subscription, so report api_key/estimated instead of claiming
        # subscription/unpriceable (#1923 cost provenance).
        bare_billing = cli_bare_billing(ai_config)
        # The credential present decides auth_mode, not the --bare policy:
        # a gateway token must not read as a subscription (#2867). A
        # subscription run pointed off the provider's host is priced on the
        # wrong table, so its figures are unknown, not unpriceable.
        provenance = detect_run_provenance(
            provider=ai_config.provider,
            transport=transport,
            bare=bare_billing,
            cwd=cwd,
        )
        if not provenance.first_party:
            basis = CostBasis.UNKNOWN
        elif bare_billing:
            basis = CostBasis.ESTIMATED
        else:
            basis = CostBasis.UNPRICEABLE
        return ResolvedTransportSettings(
            transport=transport,
            timeout=timeout,
            max_cost_usd=max_cost,
            cost_is_advisory=basis is not CostBasis.UNKNOWN,
            auth_mode=_auth_mode(
                credential=provenance.credential,
                bare_billing=bare_billing,
            ),
            cost_basis=basis,
        )

    timeout = (
        profiles.api.timeout
        if profiles.api.timeout is not None
        else ai_config.api_timeout
    )
    profile_cap = _profile_max_cost(ai_config)
    max_cost = profile_cap if profile_cap is not None else ai_config.max_cost_usd
    # ``ai.api_base_url`` can point an SDK call at a gateway; its figures are
    # then priced on the wrong table and the key is a gateway token (#2867).
    api_provenance = detect_run_provenance(
        provider=ai_config.provider,
        transport=transport,
        cwd=cwd,
        configured_base_url=ai_config.api_base_url,
    )
    return ResolvedTransportSettings(
        transport=transport,
        timeout=timeout if timeout is not None else DEFAULT_API_TIMEOUT,
        max_cost_usd=max_cost,
        cost_is_advisory=False,
        auth_mode=_auth_mode(credential=api_provenance.credential, bare_billing=True),
        cost_basis=(
            CostBasis.BILLED if api_provenance.first_party else CostBasis.UNKNOWN
        ),
    )


def resolve_max_cost_with_source(
    resolved: ResolvedAIConfig,
) -> tuple[float | None, ConfigSource]:
    """Return the effective cost cap and its provenance.

    Overlay (flag/env) wins, including when it stamped the profile fields.
    Otherwise a non-null transport-profile cap is ``config``, even when the
    legacy scalar was omitted from YAML (so the flat field's source is
    ``default``).

    Args:
        resolved: Config plus overlay-field provenance.

    Returns:
        ``(cap, source)`` after transport-profile resolution.
    """
    source = resolved.source_of("max_cost_usd")
    if source not in (ConfigSource.FLAG, ConfigSource.ENV):
        profile_cap = _profile_max_cost(resolved.config)
        if profile_cap is not None:
            return profile_cap, ConfigSource.CONFIG
    return resolve_transport_settings(resolved.config).max_cost_usd, source


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
