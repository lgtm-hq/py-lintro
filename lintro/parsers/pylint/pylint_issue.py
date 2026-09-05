"""Pylint issue model.

This module defines the dataclass representing a single message emitted by
pylint's ``json2`` reporter.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar

from lintro.enums.severity_level import SeverityLevel
from lintro.parsers.base_issue import BaseIssue

#: pylint's message categories mapped onto Lintro's three severity levels.
#: ``fatal``/``error`` are defects, ``warning`` and ``refactor`` are problems
#: worth acting on (``duplicate-code`` is a ``refactor``), and ``convention``
#: and ``info`` are advisory.
PYLINT_TYPE_SEVERITY: dict[str, SeverityLevel] = {
    "fatal": SeverityLevel.ERROR,
    "error": SeverityLevel.ERROR,
    "warning": SeverityLevel.WARNING,
    "refactor": SeverityLevel.WARNING,
    "convention": SeverityLevel.INFO,
    "info": SeverityLevel.INFO,
}


@dataclass
class PylintIssue(BaseIssue):
    """Represents a single pylint message.

    Attributes:
        DEFAULT_SEVERITY: Fallback when the message carries no known category.
        code: Pylint message id (``messageId``, e.g. ``"R0801"``).
        symbol: Human-readable message symbol (e.g. ``"duplicate-code"``).
        message_type: Pylint category for the message (``"refactor"``,
            ``"convention"``, ``"warning"``, ``"error"``, ``"fatal"``,
            ``"info"``).
        severity: Normalized severity, derived from ``message_type`` unless
            passed explicitly.
    """

    DEFAULT_SEVERITY: ClassVar[SeverityLevel] = SeverityLevel.WARNING

    code: str = field(default="")
    symbol: str = field(default="")
    message_type: str = field(default="")
    severity: SeverityLevel | None = field(default=None)

    def __post_init__(self) -> None:
        """Derive the normalized severity from pylint's message category."""
        if self.severity is None:
            self.severity = PYLINT_TYPE_SEVERITY.get(
                self.message_type.lower(),
                self.DEFAULT_SEVERITY,
            )
