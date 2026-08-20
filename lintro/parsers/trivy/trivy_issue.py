"""Trivy issue model for dependency vulnerabilities."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar

from lintro.parsers.base_issue import BaseIssue


@dataclass
class TrivyIssue(BaseIssue):
    """Represents a single dependency vulnerability found by Trivy.

    Trivy reports one vulnerability per (package, advisory) pair against a
    scanned lockfile or manifest. The native JSON output carries richer
    metadata than Trivy's SARIF output (see the fidelity comparison in
    ``docs/tool-analysis/trivy-analysis.md``): the aggregated vendor
    ``severity`` (CRITICAL/HIGH/MEDIUM/LOW/UNKNOWN), a structured
    ``fixed_version`` field, the installed version, and vendor advisory IDs.
    These would collapse into free-text prose under SARIF.

    Attributes:
        DISPLAY_FIELD_MAP: Mapping of display field names to attribute names.
        vuln_id: Vulnerability identifier (e.g. ``CVE-2019-14234``) used as the
            display ``code``.
        pkg_name: Name of the vulnerable package.
        installed_version: Version of the package pinned in the lockfile.
        fixed_version: Version(s) that resolve the vulnerability, if any.
        severity: Native Trivy severity string
            (``CRITICAL``/``HIGH``/``MEDIUM``/``LOW``/``UNKNOWN``).
        title: Short human-readable summary of the advisory.
        target: The scanned lockfile / manifest Trivy attributed the finding to.
    """

    DISPLAY_FIELD_MAP: ClassVar[dict[str, str]] = {
        **BaseIssue.DISPLAY_FIELD_MAP,
        "code": "vuln_id",
        "message": "message",
        "severity": "severity",
    }

    vuln_id: str = field(default="")
    pkg_name: str = field(default="")
    installed_version: str = field(default="")
    fixed_version: str | None = field(default=None)
    severity: str | None = field(default=None)
    title: str = field(default="")
    target: str = field(default="")

    def __post_init__(self) -> None:
        """Compose the display message from the vulnerability metadata."""
        self.message = self._get_message()

    def _get_message(self) -> str:
        """Build the formatted issue message.

        Returns:
            str: Formatted message including package, installed version, a short
                title, and the fixed version (or a "no fix available" note).
        """
        pkg = self.pkg_name
        if self.installed_version:
            pkg = f"{pkg} {self.installed_version}"

        summary = self.title or self.vuln_id
        if self.fixed_version:
            remediation = f"fixed in {self.fixed_version}"
        else:
            remediation = "no fix available"

        return f"{pkg}: {summary} ({remediation})"
