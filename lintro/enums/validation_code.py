"""Stable machine-readable codes for configuration validation findings.

These codes are part of the ``lintro config validate --json`` contract:
consumers should branch on ``code`` rather than on the human-readable
``message`` text, which may be reworded at any time.
"""

from enum import StrEnum, auto


class ValidationCode(StrEnum):
    """Stable identifiers for configuration validation findings."""

    NOT_FOUND = auto()
    PARSE_ERROR = auto()
    EMPTY_CONFIG = auto()
    INVALID_TYPE = auto()
    UNKNOWN_OPTION = auto()
    DEPRECATED_OPTION = auto()
    UNKNOWN_TOOL = auto()
    MISSING_DEPENDENCY = auto()
