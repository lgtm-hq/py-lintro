"""Diff-bounded finding gate (#2711, lintro-ops milestone 0 step 0.7).

Nothing upstream checks that a finding's ``file:line`` falls inside a hunk of
the chunk that produced it: ``reject_context_findings`` (finding_parser)
polices *paths*, so a finding on a reviewed file at a line the PR never
touched passes through, and inline posting then fails or lands on an
untouched line. This module is the mechanical half of that problem (the
model-side refutation stays on lintro-ops#10): given the hunk ranges of the
chunk's diff, every parsed finding is classified by its primary location as

* ``in_diff``: the line lies inside a hunk (context lines included, which is
  what the inline-comment API accepts), kept unchanged;
* ``near_diff``: within ``near_lines`` of a hunk, re-anchored to the nearest
  changed line of that hunk (a hunk with no added lines anchors at its start);
* ``outside``: dropped and counted under ``outside_diff``;
* ``unanchored``: no line at all (``line <= 0``), kept and counted, since a
  whole-file or deleted-file finding has nothing to bound.

A finding on a file the chunk's diff does not cover is left alone: scope is
the path gate's job, and the resume queue may legitimately widen it.
Secondary occurrences are checked one by one (an outside occurrence is
dropped from the tuple, a near one re-anchored); only the primary location
decides whether the finding survives. The gate runs after parsing and before
the P1 evidence gate, and its counts ride the chunk partial into the run's
metadata, the JSON output and the run record so a drop is never silent.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING

from loguru import logger

from lintro.ai.review.context.diff_parse import split_unified_diff_by_file
from lintro.ai.review.models.finding_occurrence import FindingOccurrence

if TYPE_CHECKING:
    from lintro.ai.review.models.review_finding import ReviewFinding

__all__ = [
    "DEFAULT_NEAR_LINES",
    "DiffGate",
    "DiffGateCounts",
    "FileHunks",
    "hunks_from_diff",
]

#: Default distance (in lines) within which a finding is re-anchored rather
#: than dropped; ``ai.review_diff_gate_lines`` overrides it.
DEFAULT_NEAR_LINES = 3

_HUNK_HEADER = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", re.MULTILINE)


@dataclass(frozen=True, slots=True)
class FileHunks:
    """The new-file line ranges one chunk's diff touches in one file.

    Attributes:
        ranges: Inclusive ``(start, end)`` new-file line ranges, one per hunk.
            A hunk with no new-file lines (pure deletion) is recorded as
            ``(start, start)`` so a finding at the deletion point counts as
            in-diff.
        changed_lines: New-file numbers of added lines, the preferred
            re-anchor targets.
    """

    ranges: tuple[tuple[int, int], ...] = ()
    changed_lines: frozenset[int] = frozenset()

    def contains(self, line: int) -> bool:
        """Return whether *line* lies inside one of the hunks."""
        return any(start <= line <= end for start, end in self.ranges)

    def distance(self, line: int) -> int:
        """Return the distance from *line* to the nearest hunk (0 when inside)."""
        if not self.ranges:
            return -1
        return min(
            0 if start <= line <= end else min(abs(line - start), abs(line - end))
            for start, end in self.ranges
        )

    def nearest_changed_line(self, line: int) -> int:
        """Return the changed line closest to *line*, or the nearest hunk start."""
        candidates = self.changed_lines or frozenset(start for start, _ in self.ranges)
        return min(candidates, key=lambda candidate: (abs(candidate - line), candidate))


def hunks_from_diff(*, diff: str) -> dict[str, FileHunks]:
    """Parse a unified diff into per-file hunk ranges.

    Args:
        diff: Unified diff text, possibly spanning several files.

    Returns:
        Mapping of repository-relative path to that file's hunks. Files with
        no hunk (mode-only or binary changes) are omitted.
    """
    result: dict[str, FileHunks] = {}
    for path, section in split_unified_diff_by_file(unified_diff=diff).items():
        ranges: list[tuple[int, int]] = []
        changed: set[int] = set()
        for match in _HUNK_HEADER.finditer(section):
            start = int(match.group(1))
            count = int(match.group(2)) if match.group(2) is not None else 1
            ranges.append((start, start + count - 1) if count else (start, start))
            # Walk the hunk body to number the added lines in the new file.
            body_start = match.end()
            next_match = _HUNK_HEADER.search(section, body_start)
            body = section[body_start : next_match.start() if next_match else None]
            new_line = start
            for raw in body.splitlines()[1:]:
                if raw.startswith("+"):
                    changed.add(new_line)
                    new_line += 1
                elif raw.startswith("-") or raw.startswith("\\"):
                    continue
                else:
                    new_line += 1
        if ranges:
            result[path] = FileHunks(
                ranges=tuple(ranges),
                changed_lines=frozenset(changed),
            )
    return result


@dataclass(frozen=True, slots=True)
class DiffGateCounts:
    """How many findings the gate touched, by outcome.

    Attributes:
        outside_diff: Findings dropped because their primary location lies
            outside every hunk of their file.
        reanchored: Findings whose primary location was moved onto the nearest
            changed line of a hunk within ``near_lines``.
        unanchored: Findings kept with no line to bound (``line <= 0``).
        occurrences_dropped: Secondary occurrences dropped as outside.
    """

    outside_diff: int = 0
    reanchored: int = 0
    unanchored: int = 0
    occurrences_dropped: int = 0

    def __add__(self, other: DiffGateCounts) -> DiffGateCounts:
        """Return the element-wise sum, for folding chunk counts into a run."""
        return DiffGateCounts(
            outside_diff=self.outside_diff + other.outside_diff,
            reanchored=self.reanchored + other.reanchored,
            unanchored=self.unanchored + other.unanchored,
            occurrences_dropped=self.occurrences_dropped + other.occurrences_dropped,
        )

    def to_dict(self) -> dict[str, int]:
        """Return the counts as a plain mapping for JSON output."""
        return {
            "outside_diff": self.outside_diff,
            "reanchored": self.reanchored,
            "unanchored": self.unanchored,
            "occurrences_dropped": self.occurrences_dropped,
        }


@dataclass(slots=True)
class DiffGate:
    """Bound one chunk's findings to that chunk's hunks, keeping counts.

    Attributes:
        hunks: Per-file hunks of the chunk's diff (see :func:`hunks_from_diff`).
        near_lines: Distance within which a finding is re-anchored instead of
            dropped; ``0`` re-anchors nothing.
        counts: What the gate did so far, accumulated across :meth:`apply`.
    """

    hunks: dict[str, FileHunks]
    near_lines: int = DEFAULT_NEAR_LINES
    counts: DiffGateCounts = field(default_factory=DiffGateCounts)

    def apply(
        self,
        *,
        findings: tuple[ReviewFinding, ...],
    ) -> tuple[ReviewFinding, ...]:
        """Drop or re-anchor findings whose location the chunk's diff cannot bound.

        Args:
            findings: Parsed findings of one chunk, in reported order.

        Returns:
            The surviving findings, re-anchored where needed, in order.
        """
        kept: list[ReviewFinding] = []
        for finding in findings:
            verdict, line = self._classify(path=finding.file, line=finding.line)
            if verdict == "outside":
                self._bump(outside_diff=1)
                logger.info(
                    "Dropped finding outside the chunk diff: {path}:{line} ({title})",
                    path=finding.file,
                    line=finding.line,
                    title=finding.title,
                )
                continue
            if verdict == "unanchored":
                self._bump(unanchored=1)
            elif verdict == "near":
                self._bump(reanchored=1)
                logger.info(
                    "Re-anchored finding {path}:{old} to changed line {new}",
                    path=finding.file,
                    old=finding.line,
                    new=line,
                )
            occurrences = self._gate_occurrences(finding=finding)
            if line != finding.line or occurrences != finding.occurrences:
                finding = replace(finding, line=line, occurrences=occurrences)
            kept.append(finding)
        return tuple(kept)

    def _gate_occurrences(
        self,
        *,
        finding: ReviewFinding,
    ) -> tuple[FindingOccurrence, ...]:
        """Check each secondary occurrence on its own."""
        kept: list[FindingOccurrence] = []
        for occurrence in finding.occurrences:
            verdict, line = self._classify(path=occurrence.file, line=occurrence.line)
            if verdict == "outside":
                self._bump(occurrences_dropped=1)
                continue
            kept.append(
                (
                    occurrence
                    if line == occurrence.line
                    else FindingOccurrence(file=occurrence.file, line=line)
                ),
            )
        return tuple(kept)

    def _classify(self, *, path: str, line: int) -> tuple[str, int]:
        """Return the verdict for one location and the line to keep it at."""
        if line <= 0:
            return "unanchored", line
        hunks = self.hunks.get(_normalize_path(path))
        if hunks is None:
            # Not this chunk's file: scope is the path gate's decision.
            return "in_diff", line
        if hunks.contains(line):
            return "in_diff", line
        distance = hunks.distance(line)
        if 0 < distance <= self.near_lines:
            return "near", hunks.nearest_changed_line(line)
        return "outside", line

    def _bump(self, **deltas: int) -> None:
        self.counts = self.counts + DiffGateCounts(**deltas)


def _normalize_path(path: str) -> str:
    """Normalize a model-reported path the way the path gate does."""
    return path.strip().replace("\\", "/").removeprefix("./")
