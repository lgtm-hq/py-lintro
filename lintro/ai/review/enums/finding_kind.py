"""Claim type of an entry in a review's ``findings`` array."""

from __future__ import annotations

from enum import StrEnum, auto


class FindingKind(StrEnum):
    """What kind of claim a reported entry makes (#1925).

    Questions are the pressure-release valve that makes the P1 evidence gate
    fair: suspicion the model cannot back with a concrete failure mechanism is
    asked as a question instead of inflating a severity. Questions therefore
    never affect the derived verdict and never carry a fix prompt.

    Attributes:
        FINDING: A defect claim, carrying a severity and a fix.
        QUESTION: An open question for the author, with no severity semantics.
    """

    FINDING = auto()
    QUESTION = auto()
