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
        code: Tool-specific code identifying the rule (default: FORMAT).
        fixable: Formatting findings are auto-fixable by default.
    """

    DEFAULT_SEVERITY: ClassVar[SeverityLevel] = SeverityLevel.INFO

    code: str = field(default="FORMAT")
    fixable: bool = field(default=True)
