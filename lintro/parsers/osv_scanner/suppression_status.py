"""Suppression status enum for OSV-Scanner vulnerability entries."""

from enum import StrEnum, auto


class SuppressionStatus(StrEnum):
    """Classification of a vulnerability suppression entry."""

    ACTIVE = auto()
    STALE = auto()
    EXPIRED = auto()
