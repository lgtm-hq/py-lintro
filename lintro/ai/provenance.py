"""Where a run's requests went, what credential went with them, what answered.

ADR-0011 (decision B) requires every review run to report the endpoint host
its transport was pointed at, the credential kind actually forwarded, the
models the transport reported, and a verification state that says whether
the model the run claims is the model that answered. This module owns those
facts (#2867):

- :func:`detect_run_provenance` reads the environment lintro builds for its
  provider subprocess and names the endpoint, whether it is first-party, and
  the credential. In CI the review-lanes wrapper exports the resolved lane
  row through the ``LINTRO_REVIEW_*`` variables and those win; outside it the
  provider's base-URL override and the canonical host decide.
- :class:`ModelLedger` collects what every provider call reported, through
  the single :func:`~lintro.ai.invoke.call_ai` seam (ADR-0008, invariant 2).
- :func:`verify_models` turns the ledger into a
  :class:`~lintro.ai.enums.model_verification.ModelVerification` state in
  the fixed order the ADR prescribes.
- :func:`recorded_cost_basis` decides how a CLI run's figure is read once it
  is known whether a figure was reported and where the request went.
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
    """

    endpoint: str = ""
    first_party: bool = True
    credential: CredentialKind = CredentialKind.UNKNOWN
    lane: str = ""
    allowed_models: tuple[str, ...] = ()


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


def _base_url_override(
    *,
    provider: AIProvider | None,
    transport: AITransport,
    env: Mapping[str, str],
    cwd: str | None,
) -> str:
    """Return the base-URL override the provider's transport would honour."""
    if provider is None:
        return ""
    variable = BASE_URL_ENVS.get(provider, "")
    if not variable:
        return ""
    value = env.get(variable, "").strip()
    settings_apply = transport is AITransport.CLI and provider is AIProvider.ANTHROPIC
    if value or not settings_apply:
        return value
    # The claude CLI also reads its settings files' ``env`` block, so a base
    # URL set there routes the run just as the variable would.
    return claude_settings_env_value(variable, cwd=cwd, env=env) or ""


def _credential(
    *,
    provider: AIProvider | None,
    transport: AITransport,
    bare: bool,
    env: Mapping[str, str],
    home: Path,
    cwd: str | None,
) -> CredentialKind:
    """Name the credential the transport would forward."""
    if transport is AITransport.API:
        return CredentialKind.API_KEY
    if provider is AIProvider.ANTHROPIC:
        if bare:
            return CredentialKind.API_KEY
        if env.get(CLAUDE_OAUTH_TOKEN_ENV, "").strip():
            return CredentialKind.CLAUDE_OAUTH
        if env.get(ANTHROPIC_AUTH_TOKEN_ENV, "").strip():
            return CredentialKind.ZAI_TOKEN
        if env.get(CLAUDE_API_KEY_ENV, "").strip() or claude_api_key_available(
            cwd=cwd,
            env=env,
        ):
            return CredentialKind.API_KEY
        return CredentialKind.UNKNOWN
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


def detect_run_provenance(
    *,
    provider: AIProvider | str | None,
    transport: AITransport | None,
    bare: bool = False,
    env: Mapping[str, str] | None = None,
    home: Path | None = None,
    cwd: str | None = None,
) -> RunProvenance:
    """Read the environment and say where this run's requests will go.

    The lane row the review-lanes wrapper exports (``LINTRO_REVIEW_LANE``,
    ``LINTRO_REVIEW_ENDPOINT``, ``LINTRO_REVIEW_FIRST_PARTY``,
    ``LINTRO_REVIEW_ALLOWED_MODELS``) takes precedence: in CI the manifest,
    not a host comparison, decides what is first-party (ADR-0011, A).
    Outside CI an unset base-URL override means the provider's own
    endpoint, and a set one is first-party only when it names that host.

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
        ),
    )
    canonical = FIRST_PARTY_HOSTS.get(member, "") if member is not None else ""
    endpoint = (
        host_of(environment.get(REVIEW_ENDPOINT_ENV, "")) or override_host or canonical
    )
    first_party = _env_bool(environment.get(REVIEW_FIRST_PARTY_ENV))
    if first_party is None:
        first_party = not override_host or override_host == canonical
    return RunProvenance(
        endpoint=endpoint,
        first_party=first_party,
        credential=_credential(
            provider=member,
            transport=effective_transport,
            bare=bare,
            env=environment,
            home=home if home is not None else Path.home(),
            cwd=cwd,
        ),
        lane=environment.get(REVIEW_LANE_ENV, "").strip(),
        allowed_models=_env_list(environment.get(REVIEW_ALLOWED_MODELS_ENV)),
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


@dataclass(slots=True)
class ModelLedger:
    """What every provider call of one run reported about its model.

    Attributes:
        calls: One entry per completed call, each the models that call
            reported; an empty entry is a call that reported none.
        cost_reported: Whether any call's transport reported a cost figure
            of its own, which decides ``list_price_shadow`` against
            ``unpriceable`` on the subscription CLI.
    """

    calls: list[tuple[ReportedModel, ...]] = field(default_factory=list)
    cost_reported: bool = False

    def record(self, response: AIResponse) -> None:
        """Record one completed call.

        Args:
            response: The provider response.
        """
        self.calls.append(tuple(response.reported_models))
        if response.cost_reported:
            self.cost_reported = True

    @property
    def reported_models(self) -> tuple[str, ...]:
        """Return every distinct reported model name, in first-seen order."""
        seen: dict[str, None] = {}
        for call in self.calls:
            for model in call:
                seen.setdefault(model.name, None)
        return tuple(seen)


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


def verify_models(
    *,
    requested: str,
    calls: Sequence[Sequence[ReportedModel]],
    first_party: bool,
    allowed: Sequence[str] = (),
) -> ModelVerification | None:
    """Assign the run's model-verification state (ADR-0011, decision B).

    Args:
        requested: The model the run asked for.
        calls: What each completed call reported; an empty entry is a call
            that reported no model usage.
        first_party: Whether the endpoint is the provider's own.
        allowed: Models the lane allows besides *requested*. Empty means
            only the requested model is allowed.

    Returns:
        The state, or ``None`` when no call completed: such a round has no
        state of its own and the verdict rests on the rounds it reuses.
    """
    if not calls:
        return None
    if not first_party:
        return ModelVerification.UNVERIFIABLE
    wanted = _normalize(requested)
    allowed_names = {_normalize(name) for name in allowed} | {wanted}
    mismatch = False
    unreported = False
    for call in calls:
        if not call:
            unreported = True
            continue
        names = {_normalize(model.model) for model in call} | {
            _normalize(model.name) for model in call
        }
        if wanted not in names or any(
            _normalize(model.name) not in allowed_names
            and _normalize(model.model) not in allowed_names
            for model in call
        ):
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
        ``billed`` and ``estimated`` unchanged; otherwise ``unknown`` off a
        first-party endpoint, ``list_price_shadow`` when a figure was
        reported, and ``unpriceable`` when none was.
    """
    if configured in (CostBasis.BILLED, CostBasis.ESTIMATED):
        return configured
    if not first_party:
        return CostBasis.UNKNOWN
    return CostBasis.LIST_PRICE_SHADOW if cost_reported else CostBasis.UNPRICEABLE
