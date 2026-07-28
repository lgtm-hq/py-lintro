"""Tier 2: prove a provider can actually complete a call, end to end.

Tier 1 proves the flags still exist. It cannot prove the CLI still *behaves* — a
renamed JSON field, a changed exit convention, or a credential that authenticates
but has no credits all pass a ``--help`` check and fail every real review. Only an
invocation catches those, so this tier makes one.

It costs quota, so it is opt-in and scheduled rather than run on the pull-request
hot path, and it walks the full chain before spending anything:

    is_available()  ->  check_liveness()  ->  invoke

Each link that fails short-circuits to a *visible* skip naming the link. On CLI
transport the liveness link is presence-only by design -- it proves the binary is
runnable and still advertises the flags lintro sends, but it cannot see a depleted
subscription. That is precisely why the invocation below matters: it is the only
step that can distinguish a usable credential from a merely present one.
"""

from __future__ import annotations

import asyncio

import pytest
from assertpy import assert_that

from lintro.ai.config import AIConfig
from lintro.ai.enums import AITransport
from lintro.ai.exceptions import AIAuthenticationError
from lintro.ai.liveness import LIVENESS_TIMEOUT, LivenessResult, LivenessState
from lintro.ai.provider_enum import AIProvider
from lintro.ai.providers import get_provider
from lintro.ai.providers.base import BaseAIProvider
from tests.contract.gating import unmet_precondition

pytestmark = pytest.mark.contract_tier2

#: Prompt for the smoke invocation. Trivially cheap, but the answer is checkable,
#: so a provider that returns an empty or malformed envelope is caught rather than
#: counted as a pass.
SMOKE_PROMPT = "Reply with the single word: pong"

#: Cap on the smoke response. Large enough for a word, small enough that a
#: runaway generation cannot turn a smoke test into a bill.
SMOKE_MAX_TOKENS = 32

#: Provider timeout for the smoke call. Deliberately well under pytest's 120s
#: per-test limit: a call allowed to consume the whole budget would be reported as
#: a test-harness timeout rather than as the provider failing to answer.
SMOKE_TIMEOUT = 60.0


def _build_provider(provider: AIProvider) -> BaseAIProvider:
    """Construct a CLI-transport provider, skipping visibly when impossible.

    Args:
        provider: Provider under test.

    Returns:
        The constructed provider.
    """
    config = AIConfig(
        enabled=True,
        provider=provider,
        transport=AITransport.CLI,
    )
    try:
        return get_provider(config)
    except Exception as exc:  # noqa: BLE001 - construction failure is a skip reason
        unmet_precondition(f"{provider.value} provider could not be constructed: {exc}")


def _resolve_liveness(instance: BaseAIProvider) -> LivenessResult:
    """Run the presence and liveness links, skipping visibly on failure.

    Args:
        instance: The provider under test.

    Returns:
        A liveness result in :attr:`LivenessState.OK`.
    """
    if not instance.is_available():
        unmet_precondition(f"{instance.name}: presence check failed (link 1 of 3)")

    result = asyncio.run(instance.check_liveness(timeout=LIVENESS_TIMEOUT))
    if not result.is_live:
        unmet_precondition(
            f"{instance.name}: liveness check failed (link 2 of 3) — "
            f"{result.state.value}: {result.message}",
        )
    return result


def test_liveness_states_are_reported_not_swallowed(
    cli_provider: AIProvider,
) -> None:
    """A liveness probe must always yield a classified state, never raise.

    Args:
        cli_provider: Provider under test.
    """
    instance = _build_provider(cli_provider)
    result = asyncio.run(instance.check_liveness(timeout=LIVENESS_TIMEOUT))
    assert_that(list(LivenessState)).contains(result.state)
    assert_that(result.message).is_not_empty()
    if not result.is_live:
        assert_that(result.hint).described_as(
            "a failed liveness probe must tell the operator what to do",
        ).is_not_empty()


def test_cli_transport_liveness_is_presence_only(
    cli_provider: AIProvider,
) -> None:
    """CLI liveness must not claim a quota verdict it never checked.

    A presence-only probe that reported ``quota_verified`` would recreate exactly
    the false confidence this tier exists to remove.

    Args:
        cli_provider: Provider under test.
    """
    instance = _build_provider(cli_provider)
    result = asyncio.run(instance.check_liveness(timeout=LIVENESS_TIMEOUT))
    assert_that(result.quota_verified).is_false()


def test_live_cli_completes_a_minimal_invocation(
    cli_provider: AIProvider,
) -> None:
    """A live CLI must return a non-empty, attributed response.

    Args:
        cli_provider: Provider under test.
    """
    instance = _build_provider(cli_provider)
    _resolve_liveness(instance)

    try:
        response = asyncio.run(
            instance.complete(
                SMOKE_PROMPT,
                max_tokens=SMOKE_MAX_TOKENS,
                timeout=SMOKE_TIMEOUT,
            ),
        )
    except AIAuthenticationError as exc:
        # CLI liveness is presence-only, so an unauthenticated CLI only reveals
        # itself here. That is a missing precondition (link 3 of 3), not
        # behavioural drift — and in the scheduled gate, where the credential is
        # supposed to be provided, unmet_precondition turns it into a failure.
        #
        # This branch reaches a logged-out `claude`: the CLI prints "Not logged in
        # · Please run /login" on *stdout* with empty stderr, and the transport
        # now resolves its cause as stderr-or-stdout, so the auth patterns match
        # and `AIAuthenticationError` is raised rather than a generic provider
        # error. Only that typed exception belongs here — catching a `unknown`
        # classification would encode a defect and swallow the genuine drift this
        # tier exists to detect.
        unmet_precondition(
            f"{instance.name}: CLI is not authenticated (link 3 of 3) — {exc}",
        )
    assert_that(response.content).described_as(
        f"{instance.name} returned an empty response to a trivial prompt",
    ).is_not_empty()
    # Content, not just non-emptiness: a provider that answers with a refusal, a
    # progress banner, or a wrapper envelope has still failed to complete a call.
    # Substring rather than equality -- agent CLIs are entitled to surrounding
    # prose, and pinning the exact shape would make this brittle by design.
    assert_that(response.content.casefold()).described_as(
        f"{instance.name} did not answer the prompt it was given",
    ).contains("pong")
    assert_that(response.model).is_not_empty()
