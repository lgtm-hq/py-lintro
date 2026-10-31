"""Typed structure representing a single oxfmt issue."""

from dataclasses import dataclass, field
from typing import ClassVar

from lintro.enums.severity_level import SeverityLevel
from lintro.parsers.base_issue import BaseIssue


@dataclass
class OxfmtIssue(BaseIssue):
    """Simple container for oxfmt findings.

    Attributes:
        DEFAULT_SEVERITY: Defaults to INFO (pure formatter).
        PREVIEW_FIXABLE: Include formatting findings in dry-run previews.
        code: Tool-specific code identifying the rule (default: FORMAT).
    """

    DEFAULT_SEVERITY: ClassVar[SeverityLevel] = SeverityLevel.INFO
    PREVIEW_FIXABLE: ClassVar[bool] = True

    code: str = field(default="FORMAT")
