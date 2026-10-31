"""Typed structure representing a single Prettier issue."""

from dataclasses import dataclass, field
from typing import ClassVar

from lintro.enums.severity_level import SeverityLevel
from lintro.parsers.base_issue import BaseIssue


@dataclass
class PrettierIssue(BaseIssue):
    """Simple container for Prettier findings.

    Attributes:
        DEFAULT_SEVERITY: Defaults to INFO (pure formatter).
        PREVIEW_FIXABLE: Include formatting findings in dry-run previews.
        code: Tool-specific code identifying the rule.
    """

    DEFAULT_SEVERITY: ClassVar[SeverityLevel] = SeverityLevel.INFO
    PREVIEW_FIXABLE: ClassVar[bool] = True

    code: str = field(default="")
