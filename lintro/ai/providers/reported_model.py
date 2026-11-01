"""The model a transport says answered one call (#2867).

The ``claude`` CLI envelope carries a ``modelUsage`` block keyed by model id,
each entry naming its ``canonicalModel`` and ``provider``. That block is the
only evidence of what answered; the ``model`` a provider stamps on its
response is what it asked for. The two are kept apart so a run record can
say both.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["ReportedModel", "parse_model_usage"]


@dataclass(frozen=True, slots=True)
class ReportedModel:
    """One model named by a transport's usage report.

    Attributes:
        model: The model id as the transport keyed it.
        canonical: The transport's canonical name for it; equals ``model``
            when the transport names no alias.
        provider: The provider the transport attributed the model to, as
            reported. Informational only: it is not evidence of which
            endpoint served the request (ADR-0011, Context).
    """

    model: str
    canonical: str = ""
    provider: str = ""

    @property
    def name(self) -> str:
        """Return the canonical name, falling back to the keyed id."""
        return self.canonical or self.model


def parse_model_usage(raw: object) -> tuple[ReportedModel, ...]:
    """Parse a CLI envelope's ``modelUsage`` block.

    Args:
        raw: The decoded ``modelUsage`` value, or anything else when the
            envelope carried none.

    Returns:
        One entry per model the block names, in the block's order. Anything
        that is not a mapping of non-empty string keys yields nothing, so an
        absent or malformed block reads as "no model usage reported".
    """
    if not isinstance(raw, dict):
        return ()
    reported: list[ReportedModel] = []
    for key, value in raw.items():
        if not isinstance(key, str) or not key.strip():
            continue
        canonical = ""
        provider = ""
        if isinstance(value, dict):
            raw_canonical = value.get("canonicalModel")
            raw_provider = value.get("provider")
            if isinstance(raw_canonical, str):
                canonical = raw_canonical.strip()
            if isinstance(raw_provider, str):
                provider = raw_provider.strip()
        reported.append(
            ReportedModel(
                model=key.strip(),
                canonical=canonical or key.strip(),
                provider=provider,
            ),
        )
    return tuple(reported)
