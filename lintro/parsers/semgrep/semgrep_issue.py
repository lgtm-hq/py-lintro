"""Semgrep issue model for security and code quality findings."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar

from lintro.parsers.base_issue import BaseIssue


@dataclass
class SemgrepIssue(BaseIssue):
    """Represents an issue found by Semgrep.

    Attributes:
        DISPLAY_FIELD_MAP: Mapping of display field names to attribute names.
        check_id: Rule ID that triggered this issue.
        end_line: Ending line number of the issue.
        end_column: Ending column number of the issue.
        severity: Severity level (ERROR, WARNING, INFO).
        cwe: List of CWE IDs associated with this issue.
        metadata: Additional metadata from the rule.

    Note:
        ``category`` is inherited from ``BaseIssue`` and populated from Semgrep
        rule metadata when available.
    """

    DISPLAY_FIELD_MAP: ClassVar[dict[str, str]] = {
        **BaseIssue.DISPLAY_FIELD_MAP,
        "code": "check_id",
        "severity": "severity",
    }

    check_id: str = field(default="")
    end_line: int = field(default=0)
    end_column: int = field(default=0)
    severity: str = field(default="WARNING")
    cwe: list[str] | None = field(default=None)
    metadata: dict[str, object] | None = field(default=None)

    def __post_init__(self) -> None:
        """Initialize the inherited fields with formatted message."""
        # Always format the message to include check_id and severity prefix
        self.message = self._get_message(self.message)

    def _get_message(self, base_message: str = "") -> str:
        """Get the formatted issue message with check_id and severity prefix.

        Args:
            base_message: The original message to include after the prefix.

        Returns:
            Formatted issue message including check_id and severity.
        """
        parts: list[str] = []
        if self.check_id:
            parts.append(f"[{self.check_id}]")
        if self.severity:
            parts.append(f"{self.severity}:")
        if base_message:
            parts.append(base_message)
        return " ".join(parts) if parts else ""
