"""Commitlint issue model."""

from dataclasses import dataclass, field
from typing import ClassVar

from lintro.enums.severity_level import SeverityLevel
from lintro.parsers.base_issue import BaseIssue


@dataclass
class CommitlintIssue(BaseIssue):
    """Represents a rule violation reported by commitlint.

    Commitlint validates commit messages (not files), so the inherited
    ``line``/``column`` fields are unused (left at ``0``) and the inherited
    ``file`` field is repurposed to carry the offending commit's subject
    line for display context.

    Attributes:
        DEFAULT_SEVERITY: Defaults to ERROR (commit-message validation).
        DISPLAY_FIELD_MAP: Routes ``code`` to ``rule`` and ``severity`` to
            ``level`` for the unified display contract.
        rule: Commitlint rule name that was violated (e.g. ``type-empty``).
        level: Native severity string reported by commitlint
            (``error`` or ``warning``).
    """

    DEFAULT_SEVERITY: ClassVar[SeverityLevel] = SeverityLevel.ERROR

    DISPLAY_FIELD_MAP: ClassVar[dict[str, str]] = {
        "code": "rule",
        "severity": "level",
        "fixable": "fixable",
        "message": "message",
    }

    rule: str = field(default="")
    level: str = field(default="")
