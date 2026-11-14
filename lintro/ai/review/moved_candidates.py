"""Which prior findings need verifying before they may resolve (#3466).

Five attempts (#3448, #3465, #3468, #3470, #3480) tried to decide from git's
rename and copy output, or from path metadata, where a finding went and
whether its absence meant a fix. Each rule could be bent by some history
shape (a recreated path, a similarity look-alike, a rebase, a shallow range)
into a false resolve or a thread handed to a different finding.

So git no longer closes threads. A prior open finding that no report matched
resolves on silence only when it sat **in place**: its file was not renamed,
moved, copied or deleted, its line was placed by an exact mapping, and it
carries no lineage from an earlier move. Every other one is a *candidate*:
it resolves only when the provider, asked directly
(:mod:`lintro.ai.review.moved_verification`), says the issue is gone. Rename
similarity and lineage still decide where the round looks; they never decide
that a finding is fixed.

The verdicts ride on the relocation mapping the matcher already receives at
every call site (:class:`Placements`), so the sticky, the review body, the
inline threads and the persisted state all apply the same answers. A mapping
without verdicts (a mid-run checkpoint, a round whose check did not run)
holds every candidate.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping
from typing import TYPE_CHECKING

from lintro.ai.review.enums.finding_status import FindingStatus
from lintro.ai.review.enums.moved_verdict import MovedVerdict
from lintro.ai.review.finding_identity import fingerprint_for
from lintro.ai.review.models.delta_plan import LOCATION_HOLDS, LOCATION_UNCHECKED

if TYPE_CHECKING:
    from lintro.ai.review.models.finding_record import FindingRecord
    from lintro.ai.review.models.moved_verification import MovedVerdictEntry
    from lintro.ai.review.models.review_finding import ReviewFinding
    from lintro.ai.review.models.review_state import ReviewState

__all__ = [
    "Placements",
    "moved_verdict",
    "needs_verification",
    "placements",
    "select_candidates",
    "verified_location",
]

Relocation = tuple[str, int, str]

#: The verdict labels a recorded entry may carry.
_VERDICTS = frozenset(item.value for item in MovedVerdict)


class Placements(dict[str, Relocation]):
    """The round's relocations plus the moved-finding verdicts.

    A plain ``{key: (path, line, line_sha)}`` mapping, so every matcher call
    site keeps passing it unchanged, carrying the verdicts as
    :attr:`verdicts`.

    Attributes:
        verdicts: ``{key: (verdict, path, line, line_sha)}``; the location is
            set only for a :attr:`MovedVerdict.PRESENT` answer the round
            moved the finding to.
    """

    __slots__ = ("verdicts",)

    verdicts: dict[str, tuple[MovedVerdict, str, int, str]]

    def __init__(
        self,
        items: Mapping[str, Relocation],
        *,
        verdicts: Mapping[str, tuple[MovedVerdict, str, int, str]],
    ) -> None:
        """Hold the relocations and the verdicts.

        Args:
            items: ``{key: (path, line, line_sha)}``.
            verdicts: ``{key: (verdict, path, line, line_sha)}``.
        """
        super().__init__(items)
        self.verdicts = dict(verdicts)


def placements(
    *,
    relocations: Mapping[str, Relocation] | None,
    verdicts: Iterable[MovedVerdictEntry],
) -> Mapping[str, Relocation] | None:
    """Attach a round's recorded verdicts to its relocations.

    Args:
        relocations: ``{key: (path, line, line_sha)}``, or ``None``.
        verdicts: The ``(key, verdict, path, line, line_sha)`` entries the
            round recorded.

    Returns:
        ``relocations`` unchanged when there are no verdicts, else a
        :class:`Placements` (even when nothing was relocated: a deleted
        file's finding has a verdict and no relocation).
    """
    known = {
        key: (MovedVerdict(verdict), path, line, sha)
        for key, verdict, path, line, sha in verdicts
        if verdict in _VERDICTS
    }
    if not known:
        return relocations
    return Placements(relocations or {}, verdicts=known)


def needs_verification(
    *,
    record: FindingRecord,
    relocation: Relocation | None,
    departed_paths: Collection[str] | None,
) -> bool:
    """Tell whether silence alone may no longer resolve a prior record.

    Args:
        record: The prior open record, at its stored location.
        relocation: Where the round placed it, or ``None`` when the round
            left it where it was stored.
        departed_paths: Paths that left the diff (deletes, rename sources).

    Returns:
        True unless the record sits in place: no lineage, no rename, no
        held or unplaceable location, and its file did not leave the diff.
        A record beyond the mapping cap is never a candidate: it is held
        unresolved anyway, and verifying it would defeat the cap.
    """
    if relocation is not None and relocation[2] == LOCATION_UNCHECKED:
        return False
    if record.origin_path:
        return True
    placed = record.file
    if relocation is not None:
        path, _line, sha = relocation
        if sha in LOCATION_HOLDS or not sha or path != record.file:
            return True
        placed = path
    departed = departed_paths or ()
    return record.file in departed or placed in departed


def moved_verdict(
    *,
    record: FindingRecord,
    relocations: Mapping[str, Relocation] | None,
    departed_paths: Collection[str] | None,
) -> MovedVerdict | None:
    """Return the verdict that decides an unmatched prior record, if any.

    Args:
        record: The prior open record, at its stored location.
        relocations: The round's relocations, verdicts attached or not.
        departed_paths: Paths that left the diff.

    Returns:
        ``None`` for a record in place (today's rule applies), else the
        provider's verdict, :attr:`MovedVerdict.UNKNOWN` when there is none.
    """
    relocation = relocations.get(record.key) if relocations else None
    if not needs_verification(
        record=record,
        relocation=relocation,
        departed_paths=departed_paths,
    ):
        return None
    verdicts = relocations.verdicts if isinstance(relocations, Placements) else {}
    entry = verdicts.get(record.key)
    return entry[0] if entry is not None else MovedVerdict.UNKNOWN


def verified_location(
    *,
    relocations: Mapping[str, Relocation] | None,
    key: str,
) -> Relocation | None:
    """Return the location a ``present`` answer moved a record to, if any.

    Args:
        relocations: The round's relocations, verdicts attached or not.
        key: The record's key.

    Returns:
        ``(path, line, line_sha)`` inside this round's diff, or ``None``.
    """
    if not isinstance(relocations, Placements):
        return None
    entry = relocations.verdicts.get(key)
    if entry is None or entry[0] is not MovedVerdict.PRESENT or not entry[1]:
        return None
    return entry[1], entry[2], entry[3]


def select_candidates(
    *,
    prior: ReviewState | None,
    relocations: Mapping[str, Relocation] | None,
    departed_paths: Collection[str] | None,
    findings: Iterable[ReviewFinding],
) -> tuple[FindingRecord, ...]:
    """Return the prior open records the round has to ask the provider about.

    A record one of the round's findings pairs with, under the identity the
    matcher pairs it by this round, is left out: it is matched, not absent.
    One left out that still ends up unmatched (its pairing finding was
    dropped later) has no verdict and is held, so the filter fails safe.

    Args:
        prior: State of the previous rounds, or ``None``.
        relocations: The round's relocations.
        departed_paths: Paths that left the diff.
        findings: The round's findings, notes included.

    Returns:
        The candidates, at their stored locations, in record order.
    """
    if prior is None:
        return ()
    reported = {
        fingerprint_for(file=item.file, category=item.category, title=item.title)
        for item in findings
    }
    chosen: list[FindingRecord] = []
    for record in prior.findings:
        if record.status is not FindingStatus.OPEN or not record.file:
            continue
        relocation = relocations.get(record.key) if relocations else None
        if not needs_verification(
            record=record,
            relocation=relocation,
            departed_paths=departed_paths,
        ):
            continue
        # The identity the matcher pairs the record under this round.
        pairs_as = (
            fingerprint_for(
                file=relocation[0],
                category=record.category,
                title=record.title,
            )
            if relocation is not None and relocation[2] not in LOCATION_HOLDS
            else record.pairing_fingerprint
        )
        if pairs_as not in reported:
            chosen.append(record)
    return tuple(chosen)
