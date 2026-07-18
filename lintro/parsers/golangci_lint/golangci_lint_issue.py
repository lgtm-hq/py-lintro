"""Models for golangci-lint issues."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar

from lintro.parsers.base_issue import BaseIssue


@dataclass
class GolangciLintIssue(BaseIssue):
    """Represents a golangci-lint issue.

    golangci-lint aggregates many sub-linters (errcheck, staticcheck,
    ineffassign, ...). The originating sub-linter name is surfaced as the
    issue ``code`` so it appears in lintro's unified output.

    Attributes:
        DISPLAY_FIELD_MAP: Mapping of display field names to attribute names.
        code: Originating sub-linter name (e.g., "errcheck", "staticcheck").
        level: Severity level reported by golangci-lint (e.g., "error",
            "warning"); may be empty when golangci-lint does not classify it.
        fixable: Whether golangci-lint offers an autofix for this issue.
    """

    DISPLAY_FIELD_MAP: ClassVar[dict[str, str]] = {
        **BaseIssue.DISPLAY_FIELD_MAP,
        "severity": "level",
    }

    code: str = field(default="")
    level: str | None = field(default=None)
    fixable: bool = field(default=False)
