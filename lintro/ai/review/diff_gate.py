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
from lintro.ai.review.enums.suggestion_drop_reason import SuggestionDropReason
from lintro.ai.review.models.finding_occurrence import FindingOccurrence

if TYPE_CHECKING:
    from lintro.ai.review.models.review_finding import ReviewFinding

__all__ = [
    "DEFAULT_NEAR_LINES",
    "DiffGate",
    "DiffGateCounts",
    "FileHunks",
    "Hunk",
    "hunks_from_diff",
]

#: Default distance (in lines) within which a finding is re-anchored rather
#: than dropped; ``ai.review_diff_gate_lines`` overrides it.
DEFAULT_NEAR_LINES = 3

_HUNK_HEADER = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", re.MULTILINE)
#: A diff section that changes a file without any text line: binary content
#: or file mode only. Such a file can host no line-anchored finding.
_LINELESS_SECTION = re.compile(
    r"^(?:Binary files .* differ|GIT binary patch|old mode \d+|new mode \d+)$",
    re.MULTILINE,
)


@dataclass(frozen=True, slots=True)
class Hunk:
    """One hunk's new-file span and the added lines inside it.

    Attributes:
        start: First new-file line of the hunk.
        end: Last new-file line actually present in the hunk body (a truncated
            body shortens it; a pure deletion has ``end == start``).
        changed_lines: New-file numbers of the hunk's added lines.
    """

    start: int
    end: int
    changed_lines: frozenset[int] = frozenset()

    def contains(self, line: int) -> bool:
        """Return whether *line* lies inside the hunk."""
        return self.start <= line <= self.end

    def distance(self, line: int) -> int:
        """Return the distance from *line* to the hunk (0 when inside)."""
        if self.contains(line):
            return 0
        return min(abs(line - self.start), abs(line - self.end))

    def anchor_for(self, line: int) -> int:
        """Return this hunk's changed line closest to *line*, else its start."""
        if not self.changed_lines:
            return self.start
        return min(self.changed_lines, key=lambda c: (abs(c - line), c))


@dataclass(frozen=True, slots=True)
class FileHunks:
    """The hunks one chunk's diff holds for one file.

    Attributes:
        hunks: The file's hunks in diff order.
    """

    hunks: tuple[Hunk, ...] = ()

    @property
    def ranges(self) -> tuple[tuple[int, int], ...]:
        """Return the inclusive ``(start, end)`` new-file range of each hunk."""
        return tuple((hunk.start, hunk.end) for hunk in self.hunks)

    @property
    def changed_lines(self) -> frozenset[int]:
        """Return every added line number across the hunks."""
        return frozenset().union(*(hunk.changed_lines for hunk in self.hunks))

    def contains(self, line: int) -> bool:
        """Return whether *line* lies inside one of the hunks."""
        return any(hunk.contains(line) for hunk in self.hunks)

    def nearest(self, line: int) -> Hunk | None:
        """Return the hunk closest to *line* (ties go to the earlier hunk)."""
        if not self.hunks:
            return None
        return min(self.hunks, key=lambda hunk: (hunk.distance(line), hunk.start))

    def distance(self, line: int) -> int:
        """Return the distance from *line* to the nearest hunk (0 when inside)."""
        nearest = self.nearest(line)
        return -1 if nearest is None else nearest.distance(line)

    def nearest_changed_line(self, line: int) -> int:
        """Return the re-anchor target: a changed line of the nearest hunk.

        The nearest hunk is chosen first, then its changed line closest to
        *line*, or its start when it added nothing (a pure deletion); an
        added line of a farther hunk is never chosen.
        """
        nearest = self.nearest(line)
        return line if nearest is None else nearest.anchor_for(line)


def hunks_from_diff(*, diff: str) -> dict[str, FileHunks]:
    """Parse a unified diff into per-file hunks.

    The new-file range of a hunk is taken from the lines actually present in
    its body, not from the header's declared count: a chunk cut to the token
    ceiling can end mid-hunk, and a header-trusting range would then accept
    findings on lines the model never saw. A body stops at the declared
    count, at the next hunk header or at the next ``diff --git`` header (the
    same path can contribute several sections), so file headers are never
    read as added lines.

    Args:
        diff: Unified diff text, possibly spanning several files.

    Returns:
        Mapping of repository-relative path to that file's hunks. A binary or
        mode-only section is recorded with no hunks at all, so a line-anchored
        finding on it is outside; a section that is neither (no hunk header
        and no such marker) is omitted and left to the path gate.
    """
    result: dict[str, FileHunks] = {}
    for path, section in split_unified_diff_by_file(unified_diff=diff).items():
        hunks: list[Hunk] = []
        lines = section.splitlines()
        index = 0
        while index < len(lines):
            match = _HUNK_HEADER.match(lines[index])
            index += 1
            if match is None:
                continue
            start = int(match.group(1))
            declared = int(match.group(2)) if match.group(2) is not None else 1
            new_line = start
            changed: set[int] = set()
            while index < len(lines) and new_line - start < declared:
                raw = lines[index]
                if raw.startswith(("@@", "diff --git")):
                    break
                index += 1
                if raw.startswith("+"):
                    changed.add(new_line)
                    new_line += 1
                elif raw.startswith(("-", "\\")):
                    continue
                else:
                    new_line += 1
            present = new_line - start
            end = start + present - 1 if present else start
            hunks.append(Hunk(start=start, end=end, changed_lines=frozenset(changed)))
        if hunks or _LINELESS_SECTION.search(section):
            result[path] = FileHunks(hunks=tuple(hunks))
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
            if line != finding.line:
                finding = _reanchor(finding=finding, line=line)
            if occurrences != finding.occurrences:
                finding = replace(finding, occurrences=occurrences)
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
        if not hunks.hunks:
            # A binary or mode-only section holds no line at all.
            return "outside", line
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


def _reanchor(*, finding: ReviewFinding, line: int) -> ReviewFinding:
    """Move a finding's primary line, dropping a suggestion written for the old one.

    A committable suggestion or a legacy ``suggested_code`` block replaces the
    line it was written for; after re-anchoring it would replace the new line
    instead, and the patch validator does not guard the legacy carrier. Both
    are cleared and the drop is recorded so it is never silent (#2101).
    """
    has_suggestion = finding.suggested_change is not None or bool(
        finding.suggested_code.strip(),
    )
    return replace(
        finding,
        line=line,
        suggested_code="" if has_suggestion else finding.suggested_code,
        suggested_change=None,
        suggestion_dropped=(
            SuggestionDropReason.REANCHORED
            if has_suggestion
            else finding.suggestion_dropped
        ),
    )
