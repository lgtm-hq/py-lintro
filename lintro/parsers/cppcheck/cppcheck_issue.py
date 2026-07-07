"""Cppcheck issue model."""

from dataclasses import dataclass, field
from typing import ClassVar

from lintro.parsers.base_issue import BaseIssue


@dataclass
class CppcheckIssue(BaseIssue):
    """Represents an issue found by cppcheck.

    The native cppcheck severity string is preserved verbatim in ``severity``
    so no fidelity is lost relative to the XML report. The base
    ``get_severity()`` normalizes it to lintro's tri-level scale for display,
    but the original value (``style``/``performance``/``portability``/
    ``information`` as well as ``error``/``warning``) remains available on the
    model.

    Attributes:
        DISPLAY_FIELD_MAP: Mapping of display field names to attribute names.
        severity: Native cppcheck severity (error, warning, style, performance,
            portability, information).
        code: Cppcheck check id (e.g., ``uninitvar``, ``arrayIndexOutOfBounds``).
        cwe: Associated CWE identifier when cppcheck provides one (0 if absent).
        inconclusive: Whether cppcheck flagged the finding as inconclusive.
    """

    DISPLAY_FIELD_MAP: ClassVar[dict[str, str]] = {
        **BaseIssue.DISPLAY_FIELD_MAP,
        "severity": "severity",
        "code": "code",
    }

    severity: str = field(default="error")
    code: str = field(default="")
    cwe: int = field(default=0)
    inconclusive: bool = field(default=False)
