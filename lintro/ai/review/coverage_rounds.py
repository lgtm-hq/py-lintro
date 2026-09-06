"""Round-to-round coverage state for incremental reviews (#2154).

Split out of :mod:`lintro.ai.review.coverage` (#2301). Classification of a
single round lives there; the helpers that carry model flags, invalidations
and prior coverage records from one round into the next live here. Every
function was moved verbatim and is re-exported from
:mod:`lintro.ai.review.coverage` for existing importers.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence

from lintro.ai.review.models.coverage_record import CoverageRecord
from lintro.ai.review.models.flagged_file import FlaggedFile
from lintro.ai.review.patch_hash import normalized_patch_hash

__all__ = [
    "carry_unserved_flags",
    "consume_served_flags",
    "hashes_for_diffs",
    "inherit_same_round_paths",
    "latest_coverage_by_path",
]


def carry_unserved_flags(
    *,
    new_flags: Sequence[FlaggedFile],
    prior_flags: Sequence[FlaggedFile],
    covered_now: Iterable[str],
) -> tuple[FlaggedFile, ...]:
    """Keep prior model flags whose path was not covered this round.

    A capped round can leave a ``MODEL_FLAGGED`` path unserved. Those
    flags must persist; otherwise the next classify treats the path as
    ``COVERED`` and the request disappears.

    Args:
        new_flags: Flags produced by this round's completed chunks.
        prior_flags: Flags carried from the previous trusted state.
        covered_now: Paths covered this round, including same-hash
            inheritance.

    Returns:
        This round's flags plus unserved prior flags, de-duplicated by
        path with new flags winning.
    """
    covered = set(covered_now)
    new = tuple(new_flags)
    new_paths = {flag.path for flag in new}
    unserved = tuple(
        flag
        for flag in prior_flags
        if flag.path not in covered and flag.path not in new_paths
    )
    return (*new, *unserved)


def consume_served_flags(
    *,
    prior_consumed: Sequence[tuple[str, str]],
    flags: Sequence[FlaggedFile],
    covered_now: Iterable[str],
    current_hashes: Mapping[str, str],
) -> tuple[tuple[str, str], ...]:
    """Remember ``(path, hash)`` pairs whose flag was honored this round.

    The same file+hash may be flagged only once (#2154). After the
    provider reads that path (or a same-hash sibling covers it), a
    repeat flag cannot re-queue it.

    Args:
        prior_consumed: Honored pairs from previous rounds.
        flags: Prior and newly emitted flags considered this round.
        covered_now: Paths covered this round, including inheritance.
        current_hashes: Current normalized patch hash per path.

    Returns:
        Deduplicated honored pairs, prior first.
    """
    consumed = dict.fromkeys(prior_consumed)
    covered = set(covered_now)
    for flag in flags:
        if flag.path not in covered:
            continue
        patch_hash = flag.patch_hash or current_hashes.get(flag.path, "")
        if patch_hash:
            consumed[(flag.path, patch_hash)] = None
    return tuple(consumed)


def inherit_same_round_paths(
    *,
    reviewed_now: Sequence[str],
    eligible_paths: Sequence[str],
    current_hashes: Mapping[str, str],
) -> tuple[str, ...]:
    """Credit sampled siblings that share a reviewed representative's hash.

    Args:
        reviewed_now: Paths the provider actually read.
        eligible_paths: Review-eligible paths at HEAD.
        current_hashes: Current normalized patch hash per path.

    Returns:
        ``reviewed_now`` plus same-hash siblings, de-duplicated.
    """
    reviewed = list(dict.fromkeys(reviewed_now))
    reviewed_hashes = {
        current_hashes.get(path, "")
        for path in reviewed
        if current_hashes.get(path, "")
    }
    extras = [
        path
        for path in eligible_paths
        if path not in reviewed
        and current_hashes.get(path, "") in reviewed_hashes
        and current_hashes.get(path, "")
    ]
    return (*reviewed, *extras)


def latest_coverage_by_path(
    coverage: Sequence[CoverageRecord],
) -> dict[str, CoverageRecord]:
    """Keep the highest-round record per path."""
    latest: dict[str, CoverageRecord] = {}
    for record in coverage:
        current = latest.get(record.path)
        if current is None or record.round >= current.round:
            latest[record.path] = record
    return latest


def hashes_for_diffs(*, diffs: Mapping[str, str]) -> dict[str, str]:
    """Hash each per-file unified diff.

    Args:
        diffs: Path to unified diff text.

    Returns:
        Path to normalized patch hash.
    """
    return {path: normalized_patch_hash(text) for path, text in diffs.items()}
