"""Per-tool status values in JSON reports and ``list-tools`` output."""

from __future__ import annotations

from enum import StrEnum, auto


class ToolResultStatus(StrEnum):
    """Status of one tool in a serialized run or ``list-tools`` listing.

    Attributes:
        OK: The tool ran and succeeded (or is runtime-available with a version).
        FAILED: The tool ran and failed, timed out, or reported issues.
        SKIPPED: The tool was skipped (version gate, no files, disabled).
        UNAVAILABLE: The tool binary could not be probed or is missing.
        UNKNOWN: Capability could not be determined (no snapshot or no version).
    """

    OK = auto()
    FAILED = auto()
    SKIPPED = auto()
    UNAVAILABLE = auto()
    UNKNOWN = auto()
