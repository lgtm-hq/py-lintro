"""html-validate issue model."""

from dataclasses import dataclass, field
from typing import ClassVar

from lintro.enums.severity_level import SeverityLevel
from lintro.parsers.base_issue import BaseIssue


@dataclass
class HtmlValidateIssue(BaseIssue):
    """Represents an issue found by html-validate.

    Attributes:
        DISPLAY_FIELD_MAP: Mapping of display field names to attribute names.
        DEFAULT_SEVERITY: Defaults to ERROR (validator surfaces errors by default).
        code: Rule identifier that was violated (e.g., ``wcag/h37``,
            ``no-implicit-close``).
        severity: Native severity string (``error`` or ``warning``).
        selector: CSS selector locating the offending element, when provided.
    """

    DISPLAY_FIELD_MAP: ClassVar[dict[str, str]] = {
        **BaseIssue.DISPLAY_FIELD_MAP,
        "severity": "severity",
    }

    DEFAULT_SEVERITY: ClassVar[SeverityLevel] = SeverityLevel.ERROR

    code: str = field(default="")
    severity: str = field(default="")
    selector: str = field(default="")
