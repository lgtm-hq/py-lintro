"""Vale issue model."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar

from lintro.enums.severity_level import SeverityLevel
from lintro.parsers.base_issue import BaseIssue


@dataclass
class ValeIssue(BaseIssue):
    """Represents a single issue reported by Vale.

    Vale reports prose/style violations keyed by file, each carrying a check
    name (``<Style>.<Rule>``), a severity, a human-readable message, and an
    optional documentation link.

    Attributes:
        DISPLAY_FIELD_MAP: Routes the ``code`` display column to ``check`` and
            the ``severity`` column to the native ``severity`` string.
        DEFAULT_SEVERITY: Defaults to WARNING when Vale omits a severity.
        check: Full Vale check name (e.g., ``Vale.Repetition``,
            ``Microsoft.Adverbs``).
        style: Style bundle the check belongs to (e.g., ``Vale``, ``Microsoft``,
            ``Google``), derived from the portion of ``check`` before the dot.
        severity: Native Vale severity (``error``, ``warning``, ``suggestion``).
        match: The source text that triggered the alert (may be empty).
    """

    DISPLAY_FIELD_MAP: ClassVar[dict[str, str]] = {
        "code": "check",
        "severity": "severity",
        "fixable": "fixable",
        "message": "message",
    }

    DEFAULT_SEVERITY: ClassVar[SeverityLevel] = SeverityLevel.WARNING

    check: str = field(default="")
    style: str = field(default="")
    severity: str = field(default="")
    match: str = field(default="")
