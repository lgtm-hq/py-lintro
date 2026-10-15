"""How a review run's cost figure should be interpreted (#1923, #2867)."""

from __future__ import annotations

from enum import StrEnum

__all__ = ["CostBasis", "SHADOW_COST_BASES"]


class CostBasis(StrEnum):
    """Provenance of a reported cost figure for a review run.

    Attributes:
        BILLED: Tokens were metered and priced by lintro (API transport).
        ESTIMATED: A best-effort estimate; not a bill (legacy/mixed).
        UNPRICEABLE: Subscription/CLI path where the CLI reported no figure
            at all, so lintro cannot price the call.
        LIST_PRICE_SHADOW: A first-party subscription run whose CLI envelope
            reported a list-price figure. Shown for information and used to
            settle caps as a shadow; it is not a bill (ADR-0011, B).
        UNKNOWN: A non-first-party endpoint answered. The envelope's figure
            is priced on the wrong table and is never rendered as dollars;
            caps settle on a shadow figure instead (ADR-0011, B).
    """

    BILLED = "billed"
    ESTIMATED = "estimated"
    UNPRICEABLE = "unpriceable"
    LIST_PRICE_SHADOW = "list_price_shadow"
    UNKNOWN = "unknown"


#: Bases on which a round's dollar figure is a shadow: it settles the caps
#: but is recorded as ``cost_shadow``, never as spend (ADR-0011, B).
SHADOW_COST_BASES: frozenset[CostBasis] = frozenset(
    {CostBasis.LIST_PRICE_SHADOW, CostBasis.UNKNOWN},
)
