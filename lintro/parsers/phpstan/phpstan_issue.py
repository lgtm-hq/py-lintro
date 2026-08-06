"""PHPStan issue model."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar

from lintro.enums.severity_level import SeverityLevel
from lintro.parsers.base_issue import BaseIssue


@dataclass
class PhpstanIssue(BaseIssue):
    """Represents an issue found by PHPStan.

    PHPStan reports all findings as errors, each carrying a stable
    ``identifier`` (e.g. ``arguments.count``) that maps to the online error
    reference, an optional ``tip`` with remediation guidance, and an
    ``ignorable`` flag indicating whether the error can be suppressed.

    Attributes:
        DISPLAY_FIELD_MAP: Mapping of display field names to attribute names.
        DEFAULT_SEVERITY: PHPStan findings are errors by default.
        identifier: PHPStan error identifier (e.g. ``function.notFound``).
        level: Severity level (always ``error`` for file findings).
        tip: Optional remediation hint provided by PHPStan.
        ignorable: Whether the error can be ignored via a baseline/annotation.
    """

    DISPLAY_FIELD_MAP: ClassVar[dict[str, str]] = {
        **BaseIssue.DISPLAY_FIELD_MAP,
        "code": "identifier",
        "severity": "level",
    }

    DEFAULT_SEVERITY: ClassVar[SeverityLevel] = SeverityLevel.ERROR

    identifier: str = field(default="")
    level: str = field(default="error")
    tip: str = field(default="")
    ignorable: bool = field(default=True)
