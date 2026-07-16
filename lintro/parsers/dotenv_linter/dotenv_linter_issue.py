"""dotenv-linter issue model.

This module defines the dataclass for representing issues found by
dotenv-linter, a fast linter for ``.env`` files.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar

from lintro.parsers.base_issue import BaseIssue


@dataclass
class DotenvLinterIssue(BaseIssue):
    """Represents an issue found by dotenv-linter.

    dotenv-linter reports issues in the plain-text format::

        filename:line CheckName: message

    For example::

        .env:2 LowercaseKey: The foo key should be in uppercase

    dotenv-linter does not report column numbers, so ``column`` is always 0
    (rendered as ``-`` by the unified formatter). Every check dotenv-linter
    surfaces is auto-fixable via its ``fix`` command, so ``fixable`` defaults
    to ``True``.

    Attributes:
        DISPLAY_FIELD_MAP: Mapping of display field names to attribute names.
        level: Severity level. dotenv-linter treats all findings as warnings.
        code: The check name (e.g., "LowercaseKey", "DuplicatedKey").
        fixable: Whether dotenv-linter can auto-fix this issue. Defaults to
            True since dotenv-linter's ``fix`` command addresses all checks.
    """

    DISPLAY_FIELD_MAP: ClassVar[dict[str, str]] = {
        **BaseIssue.DISPLAY_FIELD_MAP,
        "severity": "level",
    }

    level: str = field(default="warning")
    code: str = field(default="")
    fixable: bool = field(default=True)
