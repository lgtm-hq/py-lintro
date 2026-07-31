"""Review finding category enumeration."""

from __future__ import annotations

from enum import auto

from lintro.enums.hyphenated_str_enum import HyphenatedStrEnum


class ReviewCategory(HyphenatedStrEnum):
    """Categories for checklist items and review findings."""

    LOGIC_BUG = auto()
    SILENT_FAILURE = auto()
    INTEGRATION = auto()
    TEST_GAP = auto()
    CONTRACT_DRIFT = auto()
    SECURITY = auto()
    BREAKING_CHANGE = auto()
    CODE_SMELL = auto()
    ARCHITECTURE = auto()
