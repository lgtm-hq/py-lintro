"""Buf issue model.

This module defines the BufIssue dataclass for representing issues found
by the buf Protocol Buffer linter/formatter.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar

from lintro.parsers.base_issue import BaseIssue


@dataclass
class BufIssue(BaseIssue):
    """Represents an issue found by buf.

    buf emits line-delimited JSON objects for lint violations, each carrying
    a start/end position, a rule identifier (``type``) and a message. Compile
    (parse) errors are surfaced with the ``COMPILE`` rule id, and formatting
    differences (from ``buf format``) are surfaced with the ``FORMAT`` rule id.

    Attributes:
        DISPLAY_FIELD_MAP: Mapping of display field names to attribute names.
        level: Severity level (error, warning).
        code: Rule identifier reported by buf (e.g. ``PACKAGE_LOWER_SNAKE_CASE``,
            ``COMPILE``, ``FORMAT``).
        end_line: Line where the violation range ends (0 when unknown).
        end_column: Column where the violation range ends (0 when unknown).
        fixable: True for formatting differences, False for lint/compile errors.
    """

    DISPLAY_FIELD_MAP: ClassVar[dict[str, str]] = {
        **BaseIssue.DISPLAY_FIELD_MAP,
        "severity": "level",
    }

    level: str = field(default="error")
    code: str = field(default="")
    end_line: int = field(default=0)
    end_column: int = field(default=0)

    fixable: bool = field(default=False, init=False)

    def __post_init__(self) -> None:
        """Report whether ``lintro fmt`` can resolve this finding.

        Only formatting differences are auto-fixable: ``buf format --write``
        rewrites them, while lint rule violations and compile errors require a
        human edit.
        """
        self.fixable = self.code == "FORMAT"
