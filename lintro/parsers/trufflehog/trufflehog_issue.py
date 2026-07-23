"""TruffleHog issue model for secret detection findings."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar

from lintro.enums.severity_level import SeverityLevel
from lintro.parsers.base_issue import BaseIssue


@dataclass
class TrufflehogIssue(BaseIssue):
    """Represents a secret detection finding from TruffleHog.

    Attributes:
        DISPLAY_FIELD_MAP: Mapping of display field names to attribute names.
        DEFAULT_SEVERITY: Defaults to ERROR (security tool).
        detector_name: Human-readable detector name (e.g. "Github", "AWS").
        detector_type: Numeric detector type identifier from TruffleHog.
        description: Description of the credential type detected.
        verified: Whether TruffleHog confirmed the credential is live. Lintro
            runs with verification disabled by default, so this is normally
            False (detected but not checked against the provider).
        decoder_name: Decoder that surfaced the secret (e.g. "PLAIN", "BASE64").
        raw: The raw secret value (redacted for display).
        redacted: A pre-redacted representation provided by TruffleHog, if any.
        source_type: Numeric source type identifier from TruffleHog.
        source_name: Human-readable source name (e.g. "trufflehog - filesystem").
        rotation_guide: URL to rotation instructions, when TruffleHog provides one.
        extra_data: Additional detector metadata (rotation guides, versions, etc.).
    """

    DISPLAY_FIELD_MAP: ClassVar[dict[str, str]] = {
        **BaseIssue.DISPLAY_FIELD_MAP,
        "code": "detector_name",
        # message uses the computed value from __post_init__ (default mapping)
    }

    DEFAULT_SEVERITY: ClassVar[SeverityLevel] = SeverityLevel.ERROR

    detector_name: str = field(default="")
    detector_type: int = field(default=0)
    description: str = field(default="")
    verified: bool = field(default=False)
    decoder_name: str = field(default="")
    raw: str = field(default="")
    redacted: str = field(default="")
    source_type: int = field(default=0)
    source_name: str = field(default="")
    rotation_guide: str = field(default="")
    extra_data: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Initialize the inherited message field."""
        self.message = self._get_message()

    def _get_message(self) -> str:
        """Get the formatted issue message.

        Returns:
            Formatted issue message with detector, verification status, and a
            redacted secret hint.
        """
        status = "verified" if self.verified else "unverified"
        redacted_hint = "[REDACTED]" if (self.raw or self.redacted) else ""
        parts = [
            f"[{self.detector_name}]" if self.detector_name else "",
            self.description,
            f"({status})",
            redacted_hint,
        ]
        return " ".join(part for part in parts if part)
