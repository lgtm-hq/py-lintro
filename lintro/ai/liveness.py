"""Credential liveness probes for AI providers.

Availability (:mod:`lintro.ai.availability`) answers a *presence* question: is
the SDK importable, is the binary on ``PATH``, is the API-key variable set? That
is necessary but nowhere near sufficient. The chain lintro actually needs is::

    is_available()  ->  check_liveness()  ->  invoke

with each step's failure short-circuiting to a **visible** skip (No-Silent-Skip),
never to a silent pass.

The step this module adds is liveness, and its non-obvious dimension is **quota**.
A depleted account is a perfectly valid credential with zero credits: it
authenticates, it lists models, and a ``GET /v1/models`` probe reports a clean
bill of health — then every real call fails with ``credit balance is too low``.
That is exactly how the AI review check stayed green for months while reviewing
nothing (#1826). So an API-transport probe is a *minimal real call* (one token),
which is the only thing that distinguishes "authed" from "authed and able to
serve". Its cost is a rounding error against the review it gates.

CLI transports do not get a real call: a subscription CLI invocation is slow and
may consume a metered turn, so their probe is presence plus the free capability
gate (``--version`` / ``--help``) that the hybrid guard already performs. Those
results carry ``quota_verified=False`` so a caller can tell a verified credential
from an assumed one, and an auth or quota error at invocation time still surfaces
as a visible failure through the same taxonomy.

Classification reuses :mod:`lintro.ai.review.errors_taxonomy` rather than
re-deriving provider error signatures: a depleted balance looks the same whether
it aborts a review or a liveness probe, and one taxonomy means one place to teach
a new provider's error shapes.
"""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from enum import StrEnum, auto
from typing import TYPE_CHECKING, Final, TypeVar

from lintro.ai.enums import AITransport

if TYPE_CHECKING:
    from collections.abc import Coroutine

    from lintro.ai.config import AIConfig
    from lintro.ai.review.errors_taxonomy import ReviewErrorKind

__all__ = [
    "LIVENESS_PROBE_PROMPT",
    "LIVENESS_TIMEOUT",
    "LivenessResult",
    "LivenessState",
    "STATE_COPY",
    "check_liveness_sync",
    "incompatible_cli_result",
    "live_result",
    "liveness_from_error",
    "liveness_state_for_kind",
    "missing_credential_result",
]

_T = TypeVar("_T")

#: Prompt for the minimal real call that probes an API credential. Deliberately
#: content-free and paired with a one-token cap: the response is discarded, only
#: the fact that the provider served it matters.
LIVENESS_PROBE_PROMPT: Final[str] = "ping"

#: Seconds allowed for a liveness probe. Short on purpose — a probe that has to
#: wait as long as a real review is not a useful pre-flight check.
LIVENESS_TIMEOUT: Final[float] = 20.0


class LivenessState(StrEnum):
    """Outcome of a credential liveness probe.

    Members:
        OK: The credential authenticated and the provider served the probe.
        MISSING_CREDENTIAL: No credential (or binary) was present to probe.
        AUTH_FAILED: The provider rejected the credential.
        NO_QUOTA: The credential is valid but has no credits or quota left —
            the "authed-but-no-quota" state a presence or ``/v1/models`` check
            cannot see.
        RATE_LIMITED: The credential is live but currently throttled, so no call
            can be served right now.
        UNREACHABLE: The provider could not be reached (5xx, timeout, transport
            failure).
        INCOMPATIBLE_CLI: The installed agent CLI cannot serve lintro's calls —
            it is below the declared version floor, or its flag surface has
            drifted away from the declared contract.
        UNKNOWN: The probe failed in a way no signature matched.
    """

    OK = auto()
    MISSING_CREDENTIAL = auto()
    AUTH_FAILED = auto()
    NO_QUOTA = auto()
    RATE_LIMITED = auto()
    UNREACHABLE = auto()
    INCOMPATIBLE_CLI = auto()
    UNKNOWN = auto()


#: Default (message, hint) copy per state. Providers may supply a more specific
#: message; the hint is what tells an operator which lever to pull.
STATE_COPY: Final[dict[LivenessState, tuple[str, str]]] = {
    LivenessState.OK: (
        "credential is live",
        "",
    ),
    LivenessState.MISSING_CREDENTIAL: (
        "no credential available to probe",
        "Set the provider API key environment variable, or authenticate the "
        "provider CLI.",
    ),
    LivenessState.AUTH_FAILED: (
        "the provider rejected the credential",
        "Rotate or re-issue the API key, or re-authenticate the provider CLI.",
    ),
    LivenessState.NO_QUOTA: (
        "the credential is valid but has no credits or quota left",
        "Top up the provider account or raise the plan quota. Presence checks "
        "cannot see this state — only a real call can.",
    ),
    LivenessState.RATE_LIMITED: (
        "the provider is currently rate-limiting this credential",
        "Retry shortly; the credential itself is usable.",
    ),
    LivenessState.UNREACHABLE: (
        "the provider could not be reached",
        "Check network egress and provider status, then retry.",
    ),
    LivenessState.INCOMPATIBLE_CLI: (
        "the installed agent CLI does not match lintro's declared contract",
        "Upgrade the agent CLI, or use `--transport api`. The contract lives in "
        "lintro/ai/providers/cli_contracts.py.",
    ),
    LivenessState.UNKNOWN: (
        "the liveness probe failed for an unrecognized reason",
        "See the cause above; re-run with debug logging if it persists.",
    ),
}


#: States whose verdict actually says something about quota. Everything else is
#: reached before quota is consulted, so a probe cannot claim to have checked it.
_QUOTA_BEARING_STATES: Final[frozenset[LivenessState]] = frozenset(
    {LivenessState.OK, LivenessState.NO_QUOTA},
)


@dataclass(frozen=True, slots=True)
class LivenessResult:
    """The result of probing one provider's credential on one transport.

    Attributes:
        provider: Provider identifier (e.g. ``"anthropic"``).
        transport: Transport that was probed, or ``None`` when unknown.
        state: Canonical probe outcome.
        message: Human-readable outcome, provider-specific when available.
        hint: Actionable next step, empty when the probe succeeded.
        quota_verified: Whether the probe actually exercised the quota dimension.
            ``True`` only for a real call; ``False`` for a presence-only probe,
            where a depleted balance would still go undetected until invocation.
    """

    provider: str
    transport: AITransport | None
    state: LivenessState
    message: str
    hint: str = ""
    quota_verified: bool = False

    @property
    def is_live(self) -> bool:
        """Return whether the credential can serve a call right now.

        Returns:
            True only for :attr:`LivenessState.OK`.
        """
        return self.state is LivenessState.OK

    def describe(self) -> str:
        """Render a single-line description for logs and skip messages.

        Returns:
            ``"<provider>/<transport>: <message>"``, with the transport omitted
            when unknown.
        """
        where = (
            f"{self.provider}/{self.transport.value}"
            if self.transport
            else self.provider
        )
        return f"{where}: {self.message}"


def liveness_state_for_kind(*, kind: ReviewErrorKind) -> LivenessState:
    """Map a canonical provider error kind onto a liveness state.

    Args:
        kind: Classification produced by
            :func:`~lintro.ai.review.errors_taxonomy.classify_provider_error`.

    Returns:
        The corresponding liveness state. ``CONTEXT_LENGTH`` and
        ``INVALID_RESPONSE`` map to :attr:`LivenessState.OK`: both mean the
        provider *did* serve the request, so the credential is demonstrably live
        and the failure is about the payload, not the account.
    """
    from lintro.ai.review.errors_taxonomy import ReviewErrorKind

    mapping: dict[ReviewErrorKind, LivenessState] = {
        ReviewErrorKind.AUTH_FAILED: LivenessState.AUTH_FAILED,
        ReviewErrorKind.INSUFFICIENT_CREDITS: LivenessState.NO_QUOTA,
        ReviewErrorKind.QUOTA_EXCEEDED: LivenessState.NO_QUOTA,
        ReviewErrorKind.RATE_LIMITED: LivenessState.RATE_LIMITED,
        ReviewErrorKind.SERVER_ERROR: LivenessState.UNREACHABLE,
        ReviewErrorKind.TIMEOUT: LivenessState.UNREACHABLE,
        ReviewErrorKind.CONTEXT_LENGTH: LivenessState.OK,
        ReviewErrorKind.INVALID_RESPONSE: LivenessState.OK,
    }
    return mapping.get(kind, LivenessState.UNKNOWN)


def liveness_from_error(
    *,
    provider: str,
    transport: AITransport | None,
    error: Exception,
    quota_verified: bool,
) -> LivenessResult:
    """Build a liveness result from a failed probe.

    Args:
        provider: Provider identifier (e.g. ``"anthropic"``).
        transport: Transport that was probed.
        error: The exception the probe raised.
        quota_verified: Whether the probe was a real call (so a quota verdict is
            trustworthy).

    Returns:
        The classified liveness result, carrying the provider's own cause text so
        the operator sees the real message, not a generic one.
    """
    from lintro.ai.review.errors_taxonomy import (
        classify_provider_error,
        resolve_cause_text,
    )

    kind = classify_provider_error(provider=provider, error=error)
    state = liveness_state_for_kind(kind=kind)
    default_message, hint = STATE_COPY[state]
    cause = resolve_cause_text(error=error).strip()
    if state is LivenessState.OK:
        # The provider answered; the probe's own payload was the problem.
        message = default_message
    else:
        message = (
            f"{default_message} (provider reported: {cause})"
            if cause
            else default_message
        )
    return LivenessResult(
        provider=provider,
        transport=transport,
        state=state,
        message=message,
        hint=hint,
        # Only two verdicts actually speak to quota: the call went through, or it
        # was refused for lack of credits. An auth rejection, a throttle, or an
        # unreachable endpoint all short-circuit before quota is consulted, so
        # claiming a quota verdict there would be inventing information.
        quota_verified=quota_verified and state in _QUOTA_BEARING_STATES,
    )


def live_result(
    *,
    provider: str,
    transport: AITransport | None,
    quota_verified: bool,
    message: str | None = None,
) -> LivenessResult:
    """Build a successful liveness result.

    Args:
        provider: Provider identifier.
        transport: Transport that was probed.
        quota_verified: Whether a real call exercised the quota dimension.
        message: Optional override for the default success message.

    Returns:
        A :class:`LivenessResult` in :attr:`LivenessState.OK`.
    """
    default_message, _hint = STATE_COPY[LivenessState.OK]
    return LivenessResult(
        provider=provider,
        transport=transport,
        state=LivenessState.OK,
        message=message or default_message,
        quota_verified=quota_verified,
    )


def missing_credential_result(
    *,
    provider: str,
    transport: AITransport | None,
    message: str | None = None,
    hint: str | None = None,
) -> LivenessResult:
    """Build a liveness result for an absent credential.

    Args:
        provider: Provider identifier.
        transport: Transport that was probed.
        message: Optional override for the default message.
        hint: Optional override for the default hint.

    Returns:
        A :class:`LivenessResult` in :attr:`LivenessState.MISSING_CREDENTIAL`.
    """
    default_message, default_hint = STATE_COPY[LivenessState.MISSING_CREDENTIAL]
    return LivenessResult(
        provider=provider,
        transport=transport,
        state=LivenessState.MISSING_CREDENTIAL,
        message=message or default_message,
        hint=hint or default_hint,
    )


def incompatible_cli_result(
    *,
    provider: str,
    message: str,
    hint: str | None = None,
) -> LivenessResult:
    """Build a liveness result for an agent CLI that broke lintro's contract.

    Args:
        provider: Provider identifier.
        message: What specifically diverged (version floor, missing flags).
        hint: Optional override for the default upgrade hint.

    Returns:
        A :class:`LivenessResult` in :attr:`LivenessState.INCOMPATIBLE_CLI`.
    """
    _default_message, default_hint = STATE_COPY[LivenessState.INCOMPATIBLE_CLI]
    return LivenessResult(
        provider=provider,
        transport=AITransport.CLI,
        state=LivenessState.INCOMPATIBLE_CLI,
        message=message,
        hint=hint or default_hint,
    )


def _run_blocking(coro: Coroutine[object, object, _T]) -> _T:
    """Run a coroutine to completion from synchronous code.

    Mirrors the executor's loop-ownership rules: own a loop when this thread has
    none, otherwise hand the coroutine to a worker thread with a fresh loop so an
    embedding caller's running loop is never disturbed.

    Args:
        coro: The coroutine to run.

    Returns:
        The coroutine's result.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


def check_liveness_sync(
    *,
    config: AIConfig,
    timeout: float = LIVENESS_TIMEOUT,
) -> LivenessResult:
    """Probe the configured provider's credential from synchronous code.

    Instantiating the provider is itself part of the chain: a missing SDK or an
    unusable provider/transport pairing is reported as a liveness failure rather
    than raised, so a caller such as ``lintro doctor`` always gets a result to
    display instead of a traceback.

    Args:
        config: The resolved AI configuration.
        timeout: Seconds allowed for the probe.

    Returns:
        The provider's liveness result.
    """
    from lintro.ai.exceptions import AIError
    from lintro.ai.providers import get_provider

    transport = config.transport
    try:
        provider = get_provider(config)
    except (AIError, ValueError) as exc:
        return LivenessResult(
            provider=(str(config.provider) if config.provider is not None else "unset"),
            transport=transport,
            state=LivenessState.MISSING_CREDENTIAL,
            message=f"provider could not be constructed: {exc}",
            hint="Install with: uv pip install 'lintro[ai]'",
        )

    return _run_blocking(provider.check_liveness(timeout=timeout))
