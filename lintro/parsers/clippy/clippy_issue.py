"""Models for Clippy issues."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar

from lintro.parsers.base_issue import BaseIssue


@dataclass
class ClippyIssue(BaseIssue):
    """Represents a Clippy linting issue.

    Attributes:
        DISPLAY_FIELD_MAP: Mapping of display field names to attribute names.
        PREVIEW_FIXABLE: Preserve previews when no per-issue fix signal exists.
        code: Clippy lint code (e.g., clippy::needless_return).
        level: Severity level (e.g., warning, error).
        end_line: Optional end line number.
        end_column: Optional end column number.
    """

    DISPLAY_FIELD_MAP: ClassVar[dict[str, str]] = {
        **BaseIssue.DISPLAY_FIELD_MAP,
        "severity": "level",
    }
    PREVIEW_FIXABLE: ClassVar[bool] = True

    code: str = field(default="")
    level: str | None = field(default=None)
    end_line: int | None = field(default=None)
    end_column: int | None = field(default=None)
