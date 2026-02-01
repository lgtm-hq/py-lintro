"""Models for cargo-deny issues."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar

from lintro.parsers.base_issue import BaseIssue


@dataclass
class CargoDenyIssue(BaseIssue):
    """Represents a cargo-deny check issue.

    cargo-deny checks for:
    - License violations (L codes)
    - Security advisories (A codes)
    - Banned dependencies (B codes)
    - Duplicate dependencies (D codes)
    - Source violations (S codes)

    Attributes:
        code: Issue code (e.g., L001, A001, B001).
        severity: Severity level (error, warning).
        crate_name: Name of the affected crate.
        crate_version: Version of the affected crate.
        advisory_id: RUSTSEC advisory ID (for security advisories).
        advisory_severity: Advisory severity (for security advisories).
        patched_versions: List of patched versions (for security advisories).
    """

    DISPLAY_FIELD_MAP: ClassVar[dict[str, str]] = {
        **BaseIssue.DISPLAY_FIELD_MAP,
        "severity": "severity",
        "code": "code",
    }

    code: str | None = field(default=None)
    severity: str | None = field(default=None)
    crate_name: str | None = field(default=None)
    crate_version: str | None = field(default=None)
    advisory_id: str | None = field(default=None)
    advisory_severity: str | None = field(default=None)
    patched_versions: list[str] | None = field(default=None)

    def __post_init__(self) -> None:
        """Initialize the message field from issue details."""
        if not self.message:
            self.message = self._build_message()

    def _build_message(self) -> str:
        """Build a formatted message from issue details.

        Returns:
            Formatted message string.
        """
        parts: list[str] = []

        if self.crate_name:
            crate_info = self.crate_name
            if self.crate_version:
                crate_info = f"{crate_info}@{self.crate_version}"
            parts.append(f"crate {crate_info}")

        if self.advisory_id:
            advisory_info = self.advisory_id
            if self.advisory_severity:
                advisory_info = f"{advisory_info} ({self.advisory_severity})"
            parts.append(advisory_info)

        if self.patched_versions:
            parts.append(f"patched in: {', '.join(self.patched_versions)}")

        return "; ".join(parts) if parts else ""
