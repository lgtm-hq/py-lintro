"""Taplo issue model.

This module defines the TaploIssue dataclass for representing issues found
by the taplo TOML linter/formatter.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar

from lintro.parsers.base_issue import BaseIssue


@dataclass
class TaploIssue(BaseIssue):
    """Represents an issue found by taplo.

    Attributes:
        DISPLAY_FIELD_MAP: Mapping of display field names to attribute names.
        PREVIEW_FIXABLE: Preserve format previews without claiming lint fixes.
        level: Severity level (error, warning).
        code: Rule code (e.g., invalid_value, expected_table_array).
    """

    DISPLAY_FIELD_MAP: ClassVar[dict[str, str]] = {
        **BaseIssue.DISPLAY_FIELD_MAP,
        "severity": "level",
    }
    PREVIEW_FIXABLE: ClassVar[bool] = True

    level: str = field(default="error")
    code: str = field(default="")
