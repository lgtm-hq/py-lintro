"""ktlint issue model.

This module defines the dataclass for representing issues found by ktlint,
an anti-bikeshedding Kotlin linter with a built-in formatter.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar

from lintro.enums.severity_level import SeverityLevel
from lintro.parsers.base_issue import BaseIssue


@dataclass
class KtlintIssue(BaseIssue):
    """Represents an issue found by ktlint.

    ktlint's ``--reporter=json`` output groups errors by file:
    ``[{"file": ..., "errors": [{"line", "column", "message", "rule"}]}]``.
    Each error carries a rule id (e.g. ``standard:filename``) but no
    per-issue severity — ktlint treats every finding as an error and its
    JSON reporter does not expose whether a rule is auto-correctable. The
    fixability of a rule is therefore only determined at fix time by
    re-running ktlint with ``--format``.

    Attributes:
        DISPLAY_FIELD_MAP: Mapping of display field names to attribute names.
            Routes the display ``code`` column to the ktlint ``rule`` id.
        DEFAULT_SEVERITY: ktlint reports findings as errors.
        rule: The ktlint rule id (e.g. "standard:filename").
    """

    DISPLAY_FIELD_MAP: ClassVar[dict[str, str]] = {
        **BaseIssue.DISPLAY_FIELD_MAP,
        "code": "rule",
    }

    DEFAULT_SEVERITY: ClassVar[SeverityLevel] = SeverityLevel.ERROR

    rule: str = field(default="")
