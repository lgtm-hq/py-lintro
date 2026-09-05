"""Self-reported evidence basis for a review finding."""

from __future__ import annotations

from enum import StrEnum, auto


class EvidenceStyle(StrEnum):
    """How a finding was arrived at, as reported by the model (#1925).

    Verdict suppression and severity down-ranking are intentionally out of
    scope for v1; the sole behavioral effect is the verify-first caution line
    added to prompts for :data:`SPECULATIVE` findings.

    Attributes:
        DIFF_LOCAL: Established from the diff hunk alone.
        CROSS_FILE: Established by tracing code outside the diff hunk.
        SPECULATIVE: Inferred rather than verified against the code.
    """

    DIFF_LOCAL = auto()
    CROSS_FILE = auto()
    SPECULATIVE = auto()

    @classmethod
    def coerce(cls, raw: object) -> EvidenceStyle:
        """Parse an untrusted label to a member, defaulting to ``DIFF_LOCAL``.

        The single parser for this field, shared by the model-response
        normalizer and the state-blob decoder so the two can never disagree
        about whitespace, case, or which member an unknown label falls back
        to. ``DIFF_LOCAL`` is the *highest* likelihood in the convergence
        score, so an unreadable label inflates the score and fails toward
        reviewing rather than toward an early stop.

        Args:
            raw: Raw value from a model response or a persisted state blob.

        Returns:
            The matching member, or :data:`DIFF_LOCAL` when absent or
            unrecognized.
        """
        try:
            return cls(str(raw).strip().lower())
        except ValueError:
            return cls.DIFF_LOCAL
