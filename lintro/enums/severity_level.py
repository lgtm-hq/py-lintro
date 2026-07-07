"""Severity level enum definitions.

This module defines the supported severity levels for issues.
"""

from __future__ import annotations

from enum import auto

from lintro.enums.uppercase_str_enum import UppercaseStrEnum


class SeverityLevel(UppercaseStrEnum):
    """Supported severity levels for issues.

    Values are uppercase string identifiers matching enum member names.
    """

    ERROR = auto()
    WARNING = auto()
    INFO = auto()


# Alias table mapping native tool severity strings (upper-cased) to SeverityLevel.
# This lets normalize_severity_level() handle every known tool's native values
# without requiring per-parser changes.
_SEVERITY_ALIASES: dict[str, SeverityLevel] = {
    # Canonical names
    "ERROR": SeverityLevel.ERROR,
    "WARNING": SeverityLevel.WARNING,
    "INFO": SeverityLevel.INFO,
    # Common alternatives → INFO
    "NOTE": SeverityLevel.INFO,
    "HINT": SeverityLevel.INFO,
    "STYLE": SeverityLevel.INFO,
    "HELP": SeverityLevel.INFO,
    # RuboCop severities
    "CONVENTION": SeverityLevel.WARNING,
    "REFACTOR": SeverityLevel.INFO,
    "FATAL": SeverityLevel.ERROR,
    # Bandit / cargo-audit severity levels
    "HIGH": SeverityLevel.ERROR,
    "CRITICAL": SeverityLevel.ERROR,
    "MEDIUM": SeverityLevel.WARNING,
    "UNKNOWN": SeverityLevel.WARNING,
    "LOW": SeverityLevel.INFO,
    # Semgrep / Svelte-check
    "WARN": SeverityLevel.WARNING,
    # Pytest outcomes
    "FAILED": SeverityLevel.ERROR,
    "SKIPPED": SeverityLevel.INFO,
    "PASSED": SeverityLevel.INFO,
}


def normalize_severity_level(value: str | SeverityLevel) -> SeverityLevel:
    """Normalize a raw value to a SeverityLevel enum.

    Looks up the upper-cased value in the alias table, which maps every known
    native tool severity string to one of ERROR / WARNING / INFO.

    Args:
        value: str or SeverityLevel to normalize.

    Returns:
        SeverityLevel: Normalized enum value.

    Raises:
        ValueError: If the value is not a recognized severity string.
    """
    if isinstance(value, SeverityLevel):
        return value
    upper = value.upper()
    result = _SEVERITY_ALIASES.get(upper)
    if result is not None:
        return result
    supported = f"Supported levels: {sorted(_SEVERITY_ALIASES)}"
    raise ValueError(
        f"Unknown severity level: {value!r}. {supported}",
    ) from None
