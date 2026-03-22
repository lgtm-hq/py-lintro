"""Sanitize mode for prompt injection detection."""

from __future__ import annotations

from enum import StrEnum, auto


class SanitizeMode(StrEnum):
    """Controls how detected prompt injection patterns are handled.

    Attributes:
        WARN: Log a warning but continue processing (default).
        BLOCK: Skip fix generation for files with detected patterns.
        OFF: Disable injection pattern detection entirely.
    """

    WARN = auto()
    BLOCK = auto()
    OFF = auto()
