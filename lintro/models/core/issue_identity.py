"""Identity of a finding, shared by renderers and fix-result merging.

Two finding objects are the same finding when they name the same file, line,
column, code and message, whatever their concrete issue class or object
identity. The fix-mode renderers merge detected and remaining findings with
this key, and the fix convergence retry merges findings across passes with it
(#3352), so both agree on what counts as a duplicate.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Protocol, TypeVar


class KeyedIssue(Protocol):
    """The attributes of an issue that identify it."""

    @property
    def file(self) -> str:
        """Return the source file the issue refers to."""
        ...

    @property
    def line(self) -> int:
        """Return the 1-indexed line number, or 0 when absent."""
        ...

    @property
    def column(self) -> int:
        """Return the 1-indexed column number, or 0 when absent."""
        ...

    @property
    def message(self) -> str:
        """Return the human-readable issue message."""
        ...

    def get_code(self) -> str:
        """Return the rule or code identifier."""
        ...


IssueT = TypeVar("IssueT", bound=KeyedIssue)


@dataclass(frozen=True)
class IssueDedupKey:
    """Dedup key for merging issue lists.

    Frozen so instances are hashable and usable in sets. Used by both
    the CLI and file-artifact fix-mode renderers so their JSON outputs
    stay consistent, and by the fix convergence retry.

    Attributes:
        file: Source file the issue refers to.
        line: 1-indexed line number (0 when absent).
        column: 1-indexed column number (0 when absent).
        code: Rule/code identifier.
        message: Human-readable issue message.
    """

    file: str
    line: int
    column: int
    code: str
    message: str

    @classmethod
    def of(cls, issue: KeyedIssue) -> IssueDedupKey:
        """Build a key from an issue's attributes.

        Args:
            issue: The issue to extract key fields from.

        Returns:
            A hashable key representing the issue's identity.
        """
        return cls(
            file=issue.file or "",
            line=issue.line or 0,
            column=issue.column or 0,
            code=issue.get_code(),
            message=issue.message or "",
        )


def union_issues(*groups: Iterable[IssueT]) -> list[IssueT]:
    """Merge issue groups in order, keeping the first of each identity.

    Args:
        *groups: Issue sequences, earliest first.

    Returns:
        Every distinct finding once, in first-seen order.
    """
    seen: set[IssueDedupKey] = set()
    merged: list[IssueT] = []
    for group in groups:
        for issue in group:
            key = IssueDedupKey.of(issue)
            if key not in seen:
                seen.add(key)
                merged.append(issue)
    return merged


__all__ = ["IssueDedupKey", "KeyedIssue", "union_issues"]
