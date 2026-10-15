"""Where a run's requests went, what credential went with them, what answered.

ADR-0011 (decision B) requires every review run to report the endpoint host
its transport was pointed at, the credential kind actually forwarded, the
models the transport reported, and a verification state that says whether
the model the run claims is the model that answered. This module owns those
facts (#2867):

- :func:`detect_run_provenance` reads the environment lintro builds for its
  provider subprocess and names the endpoint, whether it is first-party, and
  the credential. In CI the review-lanes wrapper exports the resolved lane
  row through the ``LINTRO_REVIEW_*`` variables and those decide what is
  first-party; outside it the provider's base-URL override and the canonical
  host decide.
- :class:`ModelLedger` collects what every provider call requested and
  reported, through the single :func:`~lintro.ai.invoke.call_ai` seam
  (ADR-0008, invariant 2).
- :func:`verify_models` turns the ledger into a
  :class:`~lintro.ai.enums.model_verification.ModelVerification` state in
  the fixed order the ADR prescribes.
- :func:`recorded_cost_basis` decides how a run's figure is read once it is
  known whether a figure was reported and where the request went.
"""

from __future__ import annotations

import os
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from lintro.ai.enums import AITransport
from lintro.ai.enums.cost_basis import CostBasis
from lintro.ai.enums.credential_kind import CredentialKind
from lintro.ai.enums.model_verification import ModelVerification
from lintro.ai.provider_enum import AIProvider
from lintro.ai.providers.claude_auth import (
    CLAUDE_API_KEY_ENV,
    claude_api_key_available,
    claude_settings_env_value,
)
from lintro.ai.providers.reported_model import ReportedModel
from lintro.ai.providers.response import AIResponse

__all__ = [
    "ANTHROPIC_AUTH_TOKEN_ENV",
    "ANTHROPIC_BASE_URL_ENV",
    "BASE_URL_ENVS",
    "CLAUDE_OAUTH_TOKEN_ENV",
    "CODEX_SESSION_FILE",
    "CURSOR_API_KEY_ENV",
    "FIRST_PARTY_HOSTS",
    "OPENAI_API_KEY_ENV",
    "OPENAI_BASE_URL_ENV",
    "REVIEW_ALLOWED_MODELS_ENV",
    "REVIEW_ENDPOINT_ENV",
    "REVIEW_FIRST_PARTY_ENV",
    "REVIEW_LANE_ENV",
    "REVIEW_REPORTS_MODEL_USAGE_ENV",
    "REVIEW_SHADOW_PRICE_MODEL_ENV",
    "LedgerCall",
    "ModelLedger",
    "RunProvenance",
    "auth_mode_for",
    "current_model_ledger",
    "detect_run_provenance",
    "host_of",
    "model_ledger",
    "record_response",
    "recorded_cost_basis",
    "verify_models",
]

#: The review-lanes wrapper exports the resolved manifest row through these.
#: They are the only way a run learns its lane; nothing else may name one.
REVIEW_LANE_ENV = "LINTRO_REVIEW_LANE"
REVIEW_ENDPOINT_ENV = "LINTRO_REVIEW_ENDPOINT"
REVIEW_FIRST_PARTY_ENV = "LINTRO_REVIEW_FIRST_PARTY"
REVIEW_ALLOWED_MODELS_ENV = "LINTRO_REVIEW_ALLOWED_MODELS"
REVIEW_REPORTS_MODEL_USAGE_ENV = "LINTRO_REVIEW_REPORTS_MODEL_USAGE"
REVIEW_SHADOW_PRICE_MODEL_ENV = "LINTRO_REVIEW_SHADOW_PRICE_MODEL"

CLAUDE_OAUTH_TOKEN_ENV = "CLAUDE_CODE_OAUTH_TOKEN"  # nosec B105 - a variable name
ANTHROPIC_AUTH_TOKEN_ENV = "ANTHROPIC_AUTH_TOKEN"  # nosec B105 - a variable name
ANTHROPIC_BASE_URL_ENV = "ANTHROPIC_BASE_URL"
OPENAI_BASE_URL_ENV = "OPENAI_BASE_URL"
OPENAI_API_KEY_ENV = "OPENAI_API_KEY"
CURSOR_API_KEY_ENV = "CURSOR_API_KEY"
#: The ``codex`` CLI's ChatGPT-plan session, relative to ``$HOME``.
CODEX_SESSION_FILE = Path(".codex") / "auth.json"

#: The host each provider's own API answers on. A CLI with no base-URL
#: override (Cursor) has no entry: its endpoint is the CLI's built-in one and
#: is reported only when the lane row exports it.
FIRST_PARTY_HOSTS: Mapping[AIProvider, str] = {
    AIProvider.ANTHROPIC: "api.anthropic.com",
    AIProvider.OPENAI: "api.openai.com",
}

#: The environment variable each CLI reads as its base-URL override.
BASE_URL_ENVS: Mapping[AIProvider, str] = {
    AIProvider.ANTHROPIC: ANTHROPIC_BASE_URL_ENV,
    AIProvider.OPENAI: OPENAI_BASE_URL_ENV,
}

_AUTH_MODES: Mapping[CredentialKind, str] = {
    CredentialKind.CLAUDE_OAUTH: "subscription",
    CredentialKind.CODEX_OAUTH: "subscription",
    CredentialKind.API_KEY: "api_key",
    CredentialKind.CURSOR_API_KEY: "api_key",
    CredentialKind.ZAI_TOKEN: "gateway_token",
}

_TRUE = frozenset({"1", "true", "yes", "on"})
_FALSE = frozenset({"0", "false", "no", "off"})


@dataclass(frozen=True, slots=True)
class RunProvenance:
    """Where a run's requests go and what goes with them.

    Attributes:
        endpoint: Host the transport is pointed at; empty when unknown.
        first_party: Whether that host is the provider's own.
        credential: The credential kind present in the environment.
        lane: The review lane the wrapper exported, or empty outside CI.
        allowed_models: Models the lane row allows; empty means "the
            requested model only".
        reports_model_usage: Whether the lane's transport is declared to
            report which model answered, so an ``unreported`` round is a
            transport defect rather than the expected state.
        shadow_price_model: The first-party model whose list table prices a
            non-first-party run's shadow figure; empty means the requested
            model's own table.
    """

    endpoint: str = ""
    first_party: bool = True
    credential: CredentialKind = CredentialKind.UNKNOWN
    lane: str = ""
    allowed_models: tuple[str, ...] = ()
    reports_model_usage: bool = True
    shadow_price_model: str = ""


def host_of(url: str) -> str:
    """Return the lowercase host of *url*, with no path, port or query.

    Args:
        url: An absolute URL, a bare host, or empty.

    Returns:
        The host, or empty when *url* names none.
    """
    raw = url.strip()
    if not raw:
        return ""
    parsed = urlsplit(raw if "://" in raw else f"//{raw}")
    return (parsed.hostname or "").lower()


def _env_bool(raw: str | None) -> bool | None:
    """Parse a boolean export, or ``None`` when unset or unrecognised."""
    if raw is None:
        return None
    normalized = raw.strip().lower()
    if normalized in _TRUE:
        return True
    if normalized in _FALSE:
        return False
    return None


def _env_list(raw: str | None) -> tuple[str, ...]:
    """Parse a comma-separated export into its non-empty entries."""
    if not raw:
        return ()
    return tuple(part.strip() for part in raw.split(",") if part.strip())


def _coerce_provider(provider: AIProvider | str | None) -> AIProvider | None:
    """Return the provider member for *provider*, or ``None`` when unknown."""
    if provider is None or isinstance(provider, AIProvider):
        return provider
    try:
        return AIProvider(str(provider).strip().lower())
    except ValueError:
        return None


def _cli_env_value(
    name: str,
    *,
    provider: AIProvider | None,
    transport: AITransport,
    env: Mapping[str, str],
    cwd: str | None,
) -> str:
    """Return a variable as the provider's CLI would see it.

    The ``claude`` CLI also applies its settings files' ``env`` block, so a
    value set there reaches the process exactly as the variable would.
    """
    value = env.get(name, "").strip()
    if (
        value
        or transport is not AITransport.CLI
        or provider is not AIProvider.ANTHROPIC
    ):
        return value
    return claude_settings_env_value(name, cwd=cwd, env=env) or ""


def _base_url_override(
    *,
    provider: AIProvider | None,
    transport: AITransport,
    env: Mapping[str, str],
    cwd: str | None,
    configured_base_url: str | None,
) -> str:
    """Return the base-URL override the provider's transport would honour."""
    if transport is AITransport.API:
        return (configured_base_url or "").strip()
    if provider is None:
        return ""
    variable = BASE_URL_ENVS.get(provider, "")
    if not variable:
        return ""
    return _cli_env_value(
        variable,
        provider=provider,
        transport=transport,
        env=env,
        cwd=cwd,
    )


def _anthropic_cli_credential(
    *,
    bare: bool,
    env: Mapping[str, str],
    cwd: str | None,
) -> CredentialKind:
    """Name the credential the ``claude`` CLI would use.

    Follows Claude Code's documented precedence: the bearer token, then the
    API key or ``apiKeyHelper``, then the long-lived OAuth token. Bare mode
    never reads the OAuth token.
    """
    auth_token = _cli_env_value(
        ANTHROPIC_AUTH_TOKEN_ENV,
        provider=AIProvider.ANTHROPIC,
        transport=AITransport.CLI,
        env=env,
        cwd=cwd,
    )
    if auth_token:
        return CredentialKind.ZAI_TOKEN
    if env.get(CLAUDE_API_KEY_ENV, "").strip() or claude_api_key_available(
        cwd=cwd,
        env=env,
    ):
        return CredentialKind.API_KEY
    if not bare and env.get(CLAUDE_OAUTH_TOKEN_ENV, "").strip():
        return CredentialKind.CLAUDE_OAUTH
    return CredentialKind.UNKNOWN


def _credential(
    *,
    provider: AIProvider | None,
    transport: AITransport,
    first_party: bool,
    bare: bool,
    env: Mapping[str, str],
    home: Path,
    cwd: str | None,
) -> CredentialKind:
    """Name the credential the transport would forward."""
    if transport is AITransport.API:
        # An SDK call sends a key; off the provider's host that key is a
        # gateway token whatever variable it came from.
        return CredentialKind.API_KEY if first_party else CredentialKind.ZAI_TOKEN
    if provider is AIProvider.ANTHROPIC:
        return _anthropic_cli_credential(bare=bare, env=env, cwd=cwd)
    if provider is AIProvider.OPENAI:
        if (home / CODEX_SESSION_FILE).is_file():
            return CredentialKind.CODEX_OAUTH
        if env.get(OPENAI_API_KEY_ENV, "").strip():
            return CredentialKind.API_KEY
        return CredentialKind.UNKNOWN
    if provider is AIProvider.CURSOR:
        if env.get(CURSOR_API_KEY_ENV, "").strip():
            return CredentialKind.CURSOR_API_KEY
        return CredentialKind.UNKNOWN
    return CredentialKind.UNKNOWN


def _reports_model_usage(
    *,
    provider: AIProvider | None,
    transport: AITransport,
    env: Mapping[str, str],
) -> bool:
    """Return whether the lane's transport is declared to report its model."""
    declared = _env_bool(env.get(REVIEW_REPORTS_MODEL_USAGE_ENV))
    if declared is not None:
        return declared
    # The claude CLI envelope and every SDK response name their model; the
    # codex and cursor CLIs do not.
    return transport is AITransport.API or provider is AIProvider.ANTHROPIC


def detect_run_provenance(
    *,
    provider: AIProvider | str | None,
    transport: AITransport | None,
    bare: bool = False,
    env: Mapping[str, str] | None = None,
    home: Path | None = None,
    cwd: str | None = None,
    configured_base_url: str | None = None,
) -> RunProvenance:
    """Read the environment and say where this run's requests will go.

    The endpoint is the host the transport is actually pointed at: the
    base-URL override the CLI honours, or ``ai.api_base_url`` on the API
    transport, falling back to the lane row's declared endpoint and then to
    the provider's own host. Whether that host is first-party is decided by
    the lane row when the review-lanes wrapper exported one
    (``LINTRO_REVIEW_FIRST_PARTY``; a lane without it is not first-party),
    and otherwise by comparing the host to the provider's own (ADR-0011 A).

    Args:
        provider: The provider the run uses, by member or name.
        transport: The transport the run uses; ``None`` reads as ``api``.
        bare: Whether the ``claude`` CLI is sent ``--bare`` and so bills an
            API key regardless of any session present.
        env: Environment to read; the process environment when ``None``.
        home: Home directory holding CLI session files; ``$HOME`` when
            ``None``.
        cwd: Working directory the CLI subprocess runs in, for project-level
            settings files.
        configured_base_url: ``ai.api_base_url``, the API transport's
            endpoint override.

    Returns:
        What the run record should carry.
    """
    environment: Mapping[str, str] = os.environ if env is None else env
    member = _coerce_provider(provider)
    effective_transport = transport or AITransport.API
    override_host = host_of(
        _base_url_override(
            provider=member,
            transport=effective_transport,
            env=environment,
            cwd=cwd,
            configured_base_url=configured_base_url,
        ),
    )
    canonical = FIRST_PARTY_HOSTS.get(member, "") if member is not None else ""
    declared = host_of(environment.get(REVIEW_ENDPOINT_ENV, ""))
    endpoint = override_host or declared or canonical
    lane = environment.get(REVIEW_LANE_ENV, "").strip()
    first_party = _env_bool(environment.get(REVIEW_FIRST_PARTY_ENV))
    if first_party is None:
        # A lane row that does not say is not trusted to be first-party.
        first_party = (
            False if lane else (not override_host or override_host == canonical)
        )
    return RunProvenance(
        endpoint=endpoint,
        first_party=first_party,
        credential=_credential(
            provider=member,
            transport=effective_transport,
            first_party=first_party,
            bare=bare,
            env=environment,
            home=home if home is not None else Path.home(),
            cwd=cwd,
        ),
        lane=lane,
        allowed_models=_env_list(environment.get(REVIEW_ALLOWED_MODELS_ENV)),
        reports_model_usage=_reports_model_usage(
            provider=member,
            transport=effective_transport,
            env=environment,
        ),
        shadow_price_model=environment.get(REVIEW_SHADOW_PRICE_MODEL_ENV, "").strip(),
    )


def auth_mode_for(credential: CredentialKind | str) -> str:
    """Return the ``auth_mode`` label a credential kind implies.

    ``auth_mode`` used to be derived from the ``--bare`` policy, which said
    ``subscription`` while a gateway token was in use (ADR-0011, Context).
    It now follows the credential.

    Args:
        credential: The recorded credential kind, by member or value.

    Returns:
        ``subscription``, ``api_key`` or ``gateway_token``; empty when the
        credential is unknown, so the caller may fall back to the policy.
    """
    try:
        member = CredentialKind(str(credential).strip().lower())
    except ValueError:
        return ""
    return _AUTH_MODES.get(member, "")


@dataclass(frozen=True, slots=True)
class LedgerCall:
    """One completed provider call, as the ledger saw it.

    Attributes:
        requested: The model the call asked for: the lane default, a
            declared fallback or a custom-agent override.
        reported: The models the transport said answered; empty when it
            reported none.
    """

    requested: str
    reported: tuple[ReportedModel, ...] = ()


@dataclass(slots=True)
class ModelLedger:
    """What every provider call of one run requested and reported.

    Attributes:
        calls: One entry per completed call.
        cost_reported: Whether any call's transport reported a cost figure
            of its own, which decides ``list_price_shadow`` against
            ``unpriceable`` on the subscription CLI.
    """

    calls: list[LedgerCall] = field(default_factory=list)
    cost_reported: bool = False

    def record(self, response: AIResponse) -> None:
        """Record one completed call.

        Args:
            response: The provider response; its ``model`` is what the call
                asked for and ``reported_models`` what the transport said.
        """
        self.calls.append(
            LedgerCall(
                requested=response.model,
                reported=tuple(response.reported_models),
            ),
        )
        if response.cost_reported:
            self.cost_reported = True

    @property
    def reported_models(self) -> tuple[ReportedModel, ...]:
        """Return every distinct reported model, by name, in first-seen order."""
        seen: dict[str, ReportedModel] = {}
        for call in self.calls:
            for model in call.reported:
                seen.setdefault(model.name, model)
        return tuple(seen.values())


_LEDGER: ContextVar[ModelLedger | None] = ContextVar(
    "lintro_ai_model_ledger",
    default=None,
)


def current_model_ledger() -> ModelLedger | None:
    """Return the ledger collecting this run's calls, if one is open."""
    return _LEDGER.get()


@contextmanager
def model_ledger() -> Iterator[ModelLedger]:
    """Open a ledger that every :func:`call_ai` inside the block reports to.

    Yields:
        ModelLedger: The ledger; read it after the block.
    """
    ledger = ModelLedger()
    token = _LEDGER.set(ledger)
    try:
        yield ledger
    finally:
        _LEDGER.reset(token)


def record_response(response: AIResponse) -> None:
    """Report a completed call to the open ledger, if any.

    Args:
        response: The provider response.
    """
    ledger = _LEDGER.get()
    if ledger is not None:
        ledger.record(response)


def _normalize(name: str) -> str:
    """Normalise a model name for comparison."""
    return name.strip().lower()


def _call_matches(*, call: LedgerCall, allowed: frozenset[str]) -> bool:
    """Return whether one completed call's evidence is consistent.

    The call's own requested model must be allowed, the reported models
    must name it (aliases normalised to the canonical name), and every
    reported model must be allowed.
    """
    wanted = _normalize(call.requested)
    if wanted not in allowed:
        return False
    names = {_normalize(model.model) for model in call.reported} | {
        _normalize(model.name) for model in call.reported
    }
    if wanted not in names:
        return False
    return all(
        _normalize(model.name) in allowed or _normalize(model.model) in allowed
        for model in call.reported
    )


def verify_models(
    *,
    requested: str,
    calls: Sequence[LedgerCall],
    first_party: bool,
    allowed: Sequence[str] = (),
) -> ModelVerification | None:
    """Assign the run's model-verification state (ADR-0011, decision B).

    Args:
        requested: The run's default model.
        calls: What each completed call requested and reported.
        first_party: Whether the endpoint is the provider's own.
        allowed: The lane's allowed models, which every call's requested
            and reported models must be among. Empty means only *requested*
            is allowed.

    Returns:
        The state, or ``None`` when no call completed: such a round has no
        state of its own and the verdict rests on the rounds it reuses.
    """
    if not calls:
        return None
    if not first_party:
        return ModelVerification.UNVERIFIABLE
    allowed_names = frozenset(
        _normalize(name) for name in (allowed or (requested,)) if name.strip()
    )
    mismatch = False
    unreported = False
    for call in calls:
        if not call.reported:
            unreported = True
            continue
        if not _call_matches(call=call, allowed=allowed_names):
            mismatch = True
    if mismatch:
        return ModelVerification.MISMATCH
    if unreported:
        return ModelVerification.UNREPORTED
    return ModelVerification.VERIFIED


def recorded_cost_basis(
    *,
    configured: CostBasis,
    first_party: bool,
    cost_reported: bool,
) -> CostBasis:
    """Resolve how a finished run's figure is read (ADR-0011, decision B).

    The pre-run profile can only say ``unpriceable`` for a subscription CLI
    run; what the transport reported and where it went are known afterwards.

    Args:
        configured: The basis the transport profile resolved before the run.
        first_party: Whether the endpoint was the provider's own.
        cost_reported: Whether the transport reported a figure of its own.

    Returns:
        ``unknown`` off a first-party endpoint whatever was configured, since
        every figure is then priced on the wrong table; otherwise ``billed``
        and ``estimated`` unchanged, ``list_price_shadow`` when a figure was
        reported, and ``unpriceable`` when none was.
    """
    if not first_party:
        return CostBasis.UNKNOWN
    if configured in (CostBasis.BILLED, CostBasis.ESTIMATED):
        return configured
    return CostBasis.LIST_PRICE_SHADOW if cost_reported else CostBasis.UNPRICEABLE
