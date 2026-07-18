"""Issue model for typos output.

typos is a source-code spell checker. Each finding reports a misspelled word
(the ``typo``) together with one or more suggested ``corrections`` and the
location where the typo was found.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar

from lintro.enums.severity_level import SeverityLevel
from lintro.parsers.base_issue import BaseIssue


@dataclass
class TyposIssue(BaseIssue):
    """Represents a spelling issue found by typos.

    typos emits one JSON object per finding (``--format json``) with the
    fields ``path``, ``line_num``, ``byte_offset``, ``typo`` and
    ``corrections``. The byte offset is converted to a 1-based ``column`` for
    display consistency with other tools.

    Attributes:
        DISPLAY_FIELD_MAP: Maps the display ``severity`` key to the ``level``
            attribute so the unified formatter renders it correctly.
        DEFAULT_SEVERITY: typos reports every finding at error level.
        level: Severity level (always ``"error"`` for typos).
        typo: The misspelled word as it appears in the source.
        corrections: Suggested replacement words.
        byte_offset: Zero-based byte offset of the typo within its line.
        fixable: Whether typos can auto-correct this finding (always True).
    """

    DISPLAY_FIELD_MAP: ClassVar[dict[str, str]] = {
        **BaseIssue.DISPLAY_FIELD_MAP,
        "severity": "level",
    }

    DEFAULT_SEVERITY: ClassVar[SeverityLevel] = SeverityLevel.ERROR

    level: str = field(default="error")
    typo: str = field(default="")
    corrections: list[str] = field(default_factory=list)
    byte_offset: int = field(default=0)
    fixable: bool = field(default=True)
