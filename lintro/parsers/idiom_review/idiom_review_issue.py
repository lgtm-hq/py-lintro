"""Issue dataclass for the AI-powered ``idiom-review`` tool.

An :class:`IdiomReviewIssue` represents a single AI finding: either a
per-file idiomatic miss (Mode 1) or one location within a cross-file
duplicate-logic cluster (Mode 2). It extends :class:`BaseIssue` so the
existing unified formatters (grid, text, JSON, SARIF, CSV, HTML) render it
without any tool-specific formatting code.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar

from lintro.enums.severity_level import SeverityLevel
from lintro.parsers.base_issue import BaseIssue


@dataclass
class IdiomReviewIssue(BaseIssue):
    """A single idiom-review finding.

    Attributes:
        DISPLAY_FIELD_MAP: Mapping of display field names to attribute names.
        DEFAULT_SEVERITY: Fallback severity when confidence is unavailable.
        code: Suppressible finding code. For Mode 1 this is
            ``idiom/<language>/<pattern-name>`` (e.g.
            ``idiom/python/prefer-any``); for Mode 2 it is
            ``idiom/cross-file/duplicate-<pattern>``.
        severity: Display severity string (``WARNING``/``INFO``/``HINT``),
            derived from ``confidence`` by the parser.
        end_line: Last line of the flagged span (0 when unknown).
        confidence: AI confidence in the finding
            (``high``/``medium``/``low``).
        suggested_idiom: Short description of the idiom to prefer, or the
            suggested extraction point for duplication findings.
    """

    # Extend the base map so formatters render the idiom code and the
    # confidence-derived severity. ``code`` and ``severity`` already exist
    # in the base map; they are restated here for clarity and to guarantee
    # they survive any future base-class change.
    DISPLAY_FIELD_MAP: ClassVar[dict[str, str]] = {
        **BaseIssue.DISPLAY_FIELD_MAP,
        "code": "code",
        "severity": "severity",
    }

    DEFAULT_SEVERITY: ClassVar[SeverityLevel] = SeverityLevel.WARNING

    code: str = field(default="")
    severity: str = field(default="WARNING")
    end_line: int = field(default=0)
    confidence: str = field(default="medium")
    suggested_idiom: str = field(default="")
