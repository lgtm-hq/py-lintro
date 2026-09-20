"""Self-reported evidence basis for a review finding."""

from __future__ import annotations

from enum import StrEnum, auto


class EvidenceStyle(StrEnum):
    """How a finding was arrived at, as reported by the model (#1925).

    Two behavioural effects: the verify-first caution line prompts add for
    :data:`SPECULATIVE` findings, and the P2 evidence gate (#2723), which
    moves a ``test-gap``, ``contract-drift`` or ``code-smell`` P2 to P3
    unless the model claimed :data:`DIFF_LOCAL` for it. The gate reads the
    label as written (:meth:`parse`); :meth:`coerce`'s ``DIFF_LOCAL``
    fallback serves display and the convergence score only.

    Attributes:
        DIFF_LOCAL: Established from the diff hunk alone.
        CROSS_FILE: Established by tracing code outside the diff hunk.
        SPECULATIVE: Inferred rather than verified against the code.
    """

    DIFF_LOCAL = auto()
    CROSS_FILE = auto()
    SPECULATIVE = auto()

    @classmethod
    def parse(cls, raw: object) -> EvidenceStyle | None:
        """Parse an untrusted label to a member, or ``None`` when unreadable.

        The strict form :meth:`coerce` builds on: callers that must not
        treat an absent or unrecognized label as a claim (the P2 evidence
        gate, #2723) read this and decide the fallback themselves.

        Args:
            raw: Raw value from a model response or a persisted state blob.

        Returns:
            The matching member, or ``None`` when absent or unrecognized.
        """
        try:
            return cls(str(raw).strip().lower())
        except ValueError:
            return None

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
        parsed = cls.parse(raw)
        return cls.DIFF_LOCAL if parsed is None else parsed
