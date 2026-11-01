"""SQLFluff issue model."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar

from lintro.parsers.base_issue import BaseIssue


@dataclass
class SqlfluffIssue(BaseIssue):
    """Represents an issue found by SQLFluff.

    Attributes:
        DISPLAY_FIELD_MAP: Mapping of display field names to attribute names.
        PREVIEW_FIXABLE: Preserve format previews without claiming lint fixes.
        code: Rule code (e.g., L010, LT01).
        rule_name: Full rule name (e.g., capitalisation.keywords).
        end_line: End line number for multi-line issues.
        end_column: End column number for multi-line issues.
    """

    DISPLAY_FIELD_MAP: ClassVar[dict[str, str]] = {
        **BaseIssue.DISPLAY_FIELD_MAP,
        "code": "code",
    }
    PREVIEW_FIXABLE: ClassVar[bool] = True

    code: str = field(default="")
    rule_name: str = field(default="")
    end_line: int | None = field(default=None)
    end_column: int | None = field(default=None)
