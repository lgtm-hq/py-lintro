"""Checkov issue model for Infrastructure-as-Code misconfigurations."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar

from lintro.parsers.base_issue import BaseIssue


@dataclass
class CheckovIssue(BaseIssue):
    """Represents a failed policy check found by Checkov.

    Checkov reports one issue per failed check against an Infrastructure-as-Code
    resource. The native severity and guideline URL are only populated when
    Checkov runs with a Prisma Cloud / Bridgecrew platform API key; in the
    default offline mode both are ``None`` and lintro falls back to the
    inherited default severity and a static documentation URL.

    Attributes:
        DISPLAY_FIELD_MAP: Mapping of display field names to attribute names.
        check_id: Checkov check identifier (e.g., ``CKV_AWS_23``).
        check_name: Human-readable description of the policy.
        resource: Fully-qualified resource address (e.g.
            ``aws_security_group.allow_all``).
        check_class: Python path of the check implementation (diagnostic only).
        severity: Native severity string when available (platform key required);
            otherwise ``None``.
        guideline: Remediation guideline URL when available; otherwise ``None``.
        end_line: Last line of the offending resource block.
    """

    DISPLAY_FIELD_MAP: ClassVar[dict[str, str]] = {
        **BaseIssue.DISPLAY_FIELD_MAP,
        "code": "check_id",
        "message": "message",
        "severity": "severity",
    }

    check_id: str = field(default="")
    check_name: str = field(default="")
    resource: str = field(default="")
    check_class: str | None = field(default=None)
    severity: str | None = field(default=None)
    guideline: str | None = field(default=None)
    end_line: int | None = field(default=None)

    def __post_init__(self) -> None:
        """Compute the display message and propagate the guideline doc URL."""
        self.message = self._get_message()
        # Checkov only emits a guideline URL when run with a platform API key.
        # When present it is the most specific documentation link, so prefer it
        # over the plugin's static fallback URL.
        if self.guideline and not self.doc_url:
            self.doc_url = self.guideline

    def _get_message(self) -> str:
        """Build the formatted issue message.

        Returns:
            str: Formatted issue message including the resource attribution.
        """
        if self.resource:
            return f"{self.check_name} (resource: {self.resource})"
        return self.check_name
