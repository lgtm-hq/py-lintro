"""Issue model for OSV-Scanner output."""

from dataclasses import dataclass, field
from typing import ClassVar

from lintro.enums.severity_level import SeverityLevel
from lintro.parsers.base_issue import BaseIssue


@dataclass
class OsvScannerIssue(BaseIssue):
    """Represents a vulnerability found by OSV-Scanner.

    Attributes:
        DISPLAY_FIELD_MAP: Mapping of display field names to attribute names.
        DEFAULT_SEVERITY: Fallback severity (ERROR for security vulnerabilities).
        vuln_id: OSV vulnerability ID (e.g., GHSA-xxxx, CVE-xxxx, PYSEC-xxxx).
        severity: Severity level (CRITICAL, HIGH, MEDIUM, LOW).
        package_name: Name of the affected package.
        package_version: Installed version of the affected package.
        package_ecosystem: Ecosystem of the package (PyPI, npm, Go, etc.).
        fixed_version: Version that fixes the vulnerability, if available.
    """

    DISPLAY_FIELD_MAP: ClassVar[dict[str, str]] = {
        **BaseIssue.DISPLAY_FIELD_MAP,
        "code": "vuln_id",
    }

    DEFAULT_SEVERITY: ClassVar[SeverityLevel] = SeverityLevel.ERROR

    vuln_id: str = field(default="")
    severity: str = field(default="MEDIUM")
    package_name: str = field(default="")
    package_version: str = field(default="")
    package_ecosystem: str = field(default="")
    fixed_version: str = field(default="")

    def __post_init__(self) -> None:
        """Initialize the inherited fields with formatted message."""
        if not self.file:
            self.file = "lockfile"
        self.message = self._get_message()

    def _get_message(self) -> str:
        """Get the formatted issue message.

        Returns:
            Formatted issue message with vulnerability context.
        """
        parts = f"[{self.vuln_id}] {self.package_name}@{self.package_version}"
        if self.fixed_version:
            parts += f" (fix: {self.fixed_version})"
        return parts
