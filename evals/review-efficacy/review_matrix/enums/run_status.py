"""Outcome status for a single ``lintro review`` invocation."""

from __future__ import annotations

from enum import StrEnum, auto

__all__ = ["RunStatus"]


class RunStatus(StrEnum):
    """Why a matrix run did or did not yield comparable findings.

    Attributes:
        OK: The invocation exited cleanly and produced parseable review JSON.
        INVALID_OUTPUT: The invocation exited cleanly but its stdout could not
            be parsed as a review payload.
        FAILED: The invocation exited non-zero, timed out, or never ran.
    """

    OK = auto()
    INVALID_OUTPUT = auto()
    FAILED = auto()
