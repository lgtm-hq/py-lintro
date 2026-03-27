"""Models for OSV-Scanner vulnerability suppression entries."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from lintro.parsers.osv_scanner.suppression_status import SuppressionStatus


@dataclass(frozen=True)
class SuppressionEntry:
    """A single [[IgnoredVulns]] entry from .osv-scanner.toml.

    Attributes:
        id: Vulnerability identifier (e.g., GHSA-xxxx, CVE-xxxx).
        ignore_until: Date after which the suppression expires.
        reason: Human-readable explanation for the suppression.
    """

    id: str
    ignore_until: date
    reason: str


@dataclass(frozen=True)
class ClassifiedSuppression:
    """A suppression entry with its staleness classification.

    Attributes:
        entry: The original suppression entry.
        status: Whether the suppression is ACTIVE, STALE, or EXPIRED.
    """

    entry: SuppressionEntry
    status: SuppressionStatus
