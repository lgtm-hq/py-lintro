"""How a review run's cost figure should be interpreted (#1923)."""

from __future__ import annotations

from enum import StrEnum

__all__ = ["CostBasis"]


class CostBasis(StrEnum):
    """Provenance of a reported cost figure for a review run.

    Attributes:
        BILLED: Tokens were metered and priced by lintro (API transport).
        ESTIMATED: A best-effort estimate; not a bill (legacy/mixed).
        UNPRICEABLE: Subscription/CLI path where lintro cannot price the call.
    """

    BILLED = "billed"
    ESTIMATED = "estimated"
    UNPRICEABLE = "unpriceable"
