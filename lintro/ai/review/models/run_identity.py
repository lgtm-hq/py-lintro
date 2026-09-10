"""Who ran a review round, against what, and under which settings."""

from __future__ import annotations

from dataclasses import dataclass

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
