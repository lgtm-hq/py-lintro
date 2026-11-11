"""Lifecycle records for model identifiers a vendor no longer serves.

A retired model id is not merely unpriced: requests to it fail at the vendor.
Dropping it from a provider's ``pricing`` table alone would let it fall
through to the unknown-model path, which accepts any id, so a config naming it
would validate and then fail only after a request was attempted (#2277). A
provider therefore keeps each retired id it once registered in an explicit
``retired_models`` table, and validation refuses those ids with a message
naming the vendor's listed replacement.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

__all__ = ["RetiredModel", "retired_model_message"]


@dataclass(frozen=True)
class RetiredModel:
    """A model id the vendor retired, with the replacement it lists.

    Attributes:
        retired_on: Date the vendor stopped serving the id, as its
            deprecations page states it.
        replacement: The vendor's recommended replacement id. A provider's
            metadata requires it to be one that provider prices, so following
            the advice lands on a model lintro accounts for.
    """

    retired_on: date
    replacement: str


def retired_model_message(
    *,
    model: str,
    retired: RetiredModel,
    vendor: str,
) -> str:
    """Return the one-line refusal for a retired model id.

    Args:
        model: The retired identifier the user selected.
        retired: Its lifecycle record.
        vendor: Display name of the provider that retired it.

    Returns:
        A sentence naming the retirement date and the replacement, without a
        config path so each surface can prefix its own.
    """
    return (
        f"{model!r} was retired by {vendor} on "
        f"{retired.retired_on.isoformat()} and requests to it fail; "
        f"use {retired.replacement!r} instead"
    )
