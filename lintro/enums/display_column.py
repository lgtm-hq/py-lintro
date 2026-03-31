"""Display column enum definitions.

This module defines the standard columns used in issue table output.
"""

from __future__ import annotations

from enum import StrEnum


class DisplayColumn(StrEnum):
    """Standard columns for issue table display.

    Values are title-case strings used as table column headers.
    """

    FILE = "File"
    LINE = "Line"
    COLUMN = "Column"
    CODE = "Code"
    SEVERITY = "Severity"
    FIXABLE = "Fixable"
    MESSAGE = "Message"
    DOC_URL = "Docs"


# Standard column order for display - includes all fields since most tools report them
STANDARD_COLUMNS: list[DisplayColumn] = [
    DisplayColumn.FILE,
    DisplayColumn.LINE,
    DisplayColumn.COLUMN,
    DisplayColumn.CODE,
    DisplayColumn.SEVERITY,
    DisplayColumn.FIXABLE,
    DisplayColumn.MESSAGE,
]
