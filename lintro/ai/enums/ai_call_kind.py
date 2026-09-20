"""Kinds of provider call lintro makes, for per-kind CLI bounds (#2685)."""

from __future__ import annotations

from enum import auto

from lintro.enums.hyphenated_str_enum import HyphenatedStrEnum


class AICallKind(HyphenatedStrEnum):
    """What a provider call is for, which sets its default turn limit.

    Attributes:
        REVIEW: A review-type call: the per-chunk main call and its
            schema-recovery retry, the per-PR question pass, the depth-3
            pass, the cross-chunk synthesis pass and custom review agents.
        SUMMARY: The one-shot summary call.
        FIX: The one-shot fix call.
    """

    REVIEW = auto()
    SUMMARY = auto()
    FIX = auto()
