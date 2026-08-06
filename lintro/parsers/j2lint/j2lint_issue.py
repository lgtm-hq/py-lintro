"""Issue model for j2lint output."""

from dataclasses import dataclass, field
from typing import ClassVar

from lintro.parsers.base_issue import BaseIssue


@dataclass
class J2lintIssue(BaseIssue):
    """Represents a single j2lint issue parsed from JSON output.

    Attributes:
        DISPLAY_FIELD_MAP: Mapping of display field names to attribute names.
        level: Bucket-derived severity ("error" for entries reported under
            ``ERRORS``, "warning" for entries reported under ``WARNINGS``).
        code: Rule identifier (e.g., "S3", "V1"); empty string when absent.
        native_severity: The tool's own severity label ("HIGH", "MEDIUM",
            "LOW"), preserved for reference but not used for display mapping.
        source_line: The offending template line as reported by j2lint.
    """

    DISPLAY_FIELD_MAP: ClassVar[dict[str, str]] = {
        **BaseIssue.DISPLAY_FIELD_MAP,
        "severity": "level",
    }

    level: str = field(default="error")
    code: str = field(default="")
    native_severity: str = field(default="")
    source_line: str = field(default="")
