"""What the run-level verify pass could say about one tool (#1743).

A verify outcome has three states, not two. Two of them are verdicts — the
``CHECK`` measured the residual, or nothing needed re-measuring — and the
third is the absence of one: the check crashed, burned its deadline, or
returned without executing. That third state has to stay distinct all the way
to the display, because "we could not tell" rendered as a number is a
measurement the run never made.
"""

from __future__ import annotations

from enum import StrEnum, auto


class VerifyStatus(StrEnum):
    """The three outcomes of verifying one tool.

    Attributes:
        VERIFIED: The tool's ``CHECK`` ran over the scope and answered. Its
            findings are the authoritative residual.
        UNCHANGED: No check was needed. Nothing was rewritten, or the tool
            discovered none of the scope's files, so its pre-fix findings are
            still its post-fix findings (which may still fail the run).
        UNKNOWN: The residual could not be measured — the check raised, timed
            out, was skipped, or the tool could not be resolved at all. The
            run fails and no after-count is reported for the tool.
    """

    VERIFIED = auto()
    UNCHANGED = auto()
    UNKNOWN = auto()
