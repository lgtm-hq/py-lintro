"""Whether the model a run claims is the model that answered (#2867)."""

from __future__ import annotations

from enum import StrEnum

__all__ = ["ModelVerification"]


class ModelVerification(StrEnum):
    """The five provenance states of a run's model claim (ADR-0011, B).

    A run record carries exactly one. States are assigned in a fixed order so
    no round qualifies for two: ``LEGACY`` by the state reader; then
    ``UNVERIFIABLE`` whenever the endpoint is not first-party, whatever the
    envelope carried; then, over every completed call of the round,
    ``MISMATCH`` if any call mismatched, else ``UNREPORTED`` if any call
    carried no model usage, else ``VERIFIED``.

    Attributes:
        VERIFIED: First-party endpoint, every completed call reported usage,
            the requested model is among the reported ones and every reported
            model is allowed.
        MISMATCH: First-party endpoint and a completed call reported usage
            that does not name the requested model, or names a model the
            lane does not allow.
        UNVERIFIABLE: The endpoint is not first-party, so what the envelope
            reports cannot be trusted to describe what answered.
        UNREPORTED: Some completed call returned no model usage.
        LEGACY: The round was stored before these fields existed. Only the
            state reader assigns it; a live run never produces it.
    """

    VERIFIED = "verified"
    MISMATCH = "mismatch"
    UNVERIFIABLE = "unverifiable"
    UNREPORTED = "unreported"
    LEGACY = "legacy"
