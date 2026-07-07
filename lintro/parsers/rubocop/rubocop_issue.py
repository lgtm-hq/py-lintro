"""Model for RuboCop linting issues."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar

from lintro.enums.severity_level import SeverityLevel
from lintro.parsers.base_issue import BaseIssue


@dataclass
class RubocopIssue(BaseIssue):
    """Represents a RuboCop offense.

    Attributes:
        DEFAULT_SEVERITY: Fallback severity when RuboCop emits no level.
        code: Cop name (e.g., "Layout/SpaceInsideParens").
        severity: Native RuboCop severity (info, refactor, convention,
            warning, error, fatal).
        department: Cop department parsed from the cop name (e.g., "Layout",
            "Lint", "Style"). Empty when the cop name has no department prefix.
        correctable: Whether RuboCop can autocorrect this offense.
        corrected: Whether RuboCop already corrected this offense in a fix run.
        fixable: Whether this offense can be auto-fixed. Mirrors ``correctable``
            so the unified display shows a fixable flag.
        end_line: End line number for multi-line offenses.
        end_column: End column number for multi-line offenses.
    """

    DEFAULT_SEVERITY: ClassVar[SeverityLevel] = SeverityLevel.WARNING

    code: str = field(default="")
    severity: str = field(default="")
    department: str = field(default="")
    correctable: bool = field(default=False)
    corrected: bool = field(default=False)
    fixable: bool = field(default=False)
    end_line: int | None = field(default=None)
    end_column: int | None = field(default=None)
