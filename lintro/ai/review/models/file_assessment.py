"""Per-file assessment returned by the diff review call."""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["FileAssessment"]


@dataclass(frozen=True, slots=True)
class FileAssessment:
    """One-sentence overview of what changed in a single reviewed file.

    Severity counts are not carried here: renderers derive them from the run's
    findings so a file's counts can never drift from the findings list.

    Attributes:
        file: Repository-relative file path.
        overview: One sentence describing what changed in the file.
    """

    file: str
    overview: str
