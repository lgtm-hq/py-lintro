"""Lifecycle stages an inline finding thread can be stamped with (#1912)."""

from __future__ import annotations

from enum import StrEnum, auto

__all__ = ["LifecycleStage"]


class LifecycleStage(StrEnum):
    """What a later round verified about a finding's inline thread.

    Attributes:
        ADDRESSED: Every occurrence of the finding stopped reproducing, so the
            thread carries its outcome and may be resolved.
        PARTIAL: Some, but not all, occurrences of a collapsed pattern are
            gone. The finding is still open and the thread is never resolved.
        REGRESSED: A previously addressed finding came back. The old thread is
            stamped and stays resolved; the finding is re-raised on a fresh
            thread.
    """

    ADDRESSED = auto()
    PARTIAL = auto()
    REGRESSED = auto()
