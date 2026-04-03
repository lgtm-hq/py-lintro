"""Tool health-check status for the doctor command.

Replaces raw string literals ("ok", "missing", etc.) with a type-safe
enum so that typos are caught at import time rather than silently
producing wrong comparisons.
"""

from __future__ import annotations

from enum import StrEnum, auto


class ToolStatus(StrEnum):
    """Status of a single tool after a health check."""

    OK = auto()
    MISSING = auto()
    OUTDATED = auto()
    UNKNOWN = auto()
