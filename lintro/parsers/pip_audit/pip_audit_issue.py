"""Issue model for pip-audit output."""

from dataclasses import dataclass, field
from typing import ClassVar

from lintro.parsers.base_issue import BaseIssue


@dataclass
class PipAuditIssue(BaseIssue):
    """Represents a dependency vulnerability found by pip-audit.

    Attributes:
        DISPLAY_FIELD_MAP: Mapping of display field names to attribute names.
        vuln_id: Primary advisory ID (e.g. ``PYSEC-2019-217``, ``GHSA-...``).
        package_name: Name of the vulnerable distribution.
        package_version: Installed/pinned version of the vulnerable package.
        severity: Severity level. pip-audit's JSON output does not carry a
            severity field, so this is ``UNKNOWN`` unless populated elsewhere.
        fix_versions: Versions that resolve the vulnerability (may be empty).
        aliases: Alternate identifiers for the advisory (e.g. CVE, GHSA).
        description: Human-readable advisory description (may be empty).
    """

    DISPLAY_FIELD_MAP: ClassVar[dict[str, str]] = {
        **BaseIssue.DISPLAY_FIELD_MAP,
        "code": "vuln_id",
        "severity": "severity",
        "message": "message",
    }

    vuln_id: str = field(default="")
    package_name: str = field(default="")
    package_version: str = field(default="")
    severity: str = field(default="UNKNOWN")
    fix_versions: list[str] = field(default_factory=list)
    aliases: list[str] = field(default_factory=list)
    description: str = field(default="")

    def __post_init__(self) -> None:
        """Build the display message from the vulnerability components."""
        self.message = self._get_message()

    def _get_message(self) -> str:
        """Get the formatted issue message.

        Returns:
            str: Formatted issue message including the fix hint.
        """
        if self.fix_versions:
            fix_hint = f"fix available in {', '.join(self.fix_versions)}"
        else:
            fix_hint = "no known fix"
        return (
            f"[{self.vuln_id}] {self.package_name}@{self.package_version}: "
            f"{fix_hint}"
        )
