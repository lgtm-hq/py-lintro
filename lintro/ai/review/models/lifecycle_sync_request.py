"""Everything one lifecycle synchronization pass reads (#2305)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from lintro.ai.review.models.finding_record import FindingRecord

__all__ = ["LifecycleSyncRequest"]


@dataclass(frozen=True, kw_only=True, slots=True)
class LifecycleSyncRequest:
    """Inputs for stamping the inline threads a round settled.

    The three record groups and the context the banners read travel together
    because they all describe the same round; passing them as one value keeps
    the pass inside the eight-parameter ratchet (#2301) and makes it
    impossible to stamp one round's records with another round's sha.

    Attributes:
        resolved: Records the matcher resolved this round.
        partial: Open records of collapsed patterns with some occurrences
            gone.
        regressed: Records that came back this round; their *old* thread is
            stamped and deliberately left resolved.
        comment_bodies: Current bodies of the pull request's inline comments,
            keyed by comment id. A record whose body is absent is skipped:
            overwriting a comment whose content is unknown would destroy the
            finding it carries.
        head_sha: Head commit sha reviewed in this round.
        round_number: 1-based round number for this run.
        auto_resolve: ``review.auto_resolve``. False keeps the banner and
            skips the GraphQL mutation entirely.
        new_thread_urls: Finding key to the URL of the fresh thread carrying
            its regression.
    """

    resolved: Sequence[FindingRecord] = ()
    partial: Sequence[FindingRecord] = ()
    regressed: Sequence[FindingRecord] = ()
    comment_bodies: Mapping[int, str] = field(default_factory=dict)
    head_sha: str = ""
    round_number: int = 1
    auto_resolve: bool = True
    new_thread_urls: Mapping[str, str] = field(default_factory=dict)
