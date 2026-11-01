"""Who ran a review round, against what, and under which settings."""

from __future__ import annotations

from dataclasses import dataclass

from lintro.ai.providers.reported_model import ReportedModel

__all__ = ["RunIdentity"]


@dataclass(frozen=True, slots=True)
class RunIdentity:
    """Identifying facts about one AI review round.

    The group answers "which round was this, on which commit, run by whom".
    None of it is a measurement: every field is decided before the review
    starts, which is why it is separated from the coverage, usage and outcome
    a round produces.

    Attributes:
        round: 1-based review round number on this PR.
        timestamp: ISO 8601 UTC timestamp of the run.
        sha: Head commit sha reviewed in this round.
        model: Model identifier used for the review.
        provider: Provider name (anthropic, openai, …).
        transport: Provider transport used (for example ``api`` or ``cli``).
        auth_mode: Authentication mode used by the transport (for example
            ``api_key`` or ``subscription``).
        depth: Review depth level.
        strictness: Sensitivity preset applied.
        merge_base: ``merge-base(base, head)`` at review time (#2627), so
            the next round can tell whether the base was merged in since;
            empty on a record written before it was recorded.
        endpoint: Host the transport was pointed at (#2867, ADR-0011 B);
            empty on a record that predates it.
        first_party: Whether ``endpoint`` is the provider's own; ``None``
            when not recorded.
        credential: The credential kind actually forwarded
            (:class:`~lintro.ai.enums.credential_kind.CredentialKind`).
        reported_models: Every model the transport named as having
            answered, with its canonical name and reported provider, in
            first-seen order.
        unreported_models: Requested models of silent calls or carried
            sources, kept separately from transport-reported evidence.
        lane: The review lane the CI wrapper exported, or empty.
        model_verification: The
            :class:`~lintro.ai.enums.model_verification.ModelVerification`
            state. ``legacy`` is assigned only when a stored record carries
            none of these fields; a live run never writes it. A fully
            carried no-call round retains its source rounds' weakest state
            and their reported models, without inheriting usage or charges.
        fallback_from: The lane that failed before any chunk completed and
            handed this round to its lane (ADR-0011 D), or empty.
        fallback_reason: The error kind that lane failed with, or empty.
    """

    round: int = 1
    timestamp: str = ""
    sha: str = ""
    model: str = ""
    provider: str = ""
    transport: str = ""
    auth_mode: str = ""
    depth: int = 0
    strictness: str = ""
    merge_base: str = ""
    endpoint: str = ""
    first_party: bool | None = None
    credential: str = ""
    reported_models: tuple[ReportedModel, ...] = ()
    unreported_models: tuple[str, ...] = ()
    lane: str = ""
    model_verification: str = ""
    fallback_from: str = ""
    fallback_reason: str = ""
