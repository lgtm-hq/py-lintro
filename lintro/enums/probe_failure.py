"""Structured cause of a failed tool version probe (#2337).

An unavailable tool carries a free-form ``unavailable_reason`` for people. CI
needs a stable code to tell a tool that is absent from one whose
``--version`` call was merely slow, because the first is an image bug and the
second is infrastructure noise. The code is reported only when the cause is
known; any other probe failure leaves it unset.
"""

from __future__ import annotations

from enum import StrEnum, auto


class ProbeFailure(StrEnum):
    """Why a version probe could not establish a runnable tool.

    Attributes:
        BINARY_MISSING: The executable was not found.
        PROBE_TIMEOUT: The executable exists but its version probe timed out
            on the first attempt and again on the retry.
    """

    BINARY_MISSING = auto()
    PROBE_TIMEOUT = auto()
