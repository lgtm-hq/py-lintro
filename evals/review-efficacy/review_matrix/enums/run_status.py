"""Outcome status for a single ``lintro review`` invocation."""

from __future__ import annotations

from enum import StrEnum, auto

__all__ = ["RunStatus"]


class RunStatus(StrEnum):
    """Why a matrix run did or did not yield comparable findings.

    ``auto()`` on a :class:`~enum.StrEnum` yields the lowercased member name,
    so the serialized form in ``report.json``, ``report.md`` and
    ``runs.jsonl`` is ``ok`` / ``invalid_output`` / ``failed`` / ``incomplete``
    — never the uppercase attribute names used below.

    Attributes:
        OK: The invocation produced a parseable review payload whose
            ``findings`` list was readable. An **empty** list is ``OK``: a
            review that found nothing is a real, comparable result. The exit
            code is not part of this either — ``lintro review`` exits non-zero
            when it blocks a pull request, which is a successful review, not a
            failed run.
        INVALID_OUTPUT: The invocation's stdout could not be parsed as a
            review payload, or it claimed findings the harness could not read
            (a non-empty ``findings`` list of which no entry parsed). Reserved
            for unreadable output; it never covers a review that simply
            reported nothing.
        FAILED: The invocation exited non-zero without producing a payload,
            timed out, never ran, or returned an error envelope instead of a
            review.
        INCOMPLETE: The invocation produced a review, but the review itself
            did not cover the whole diff — a partial run, an incomplete
            findings coverage flag, or an ``incomplete`` readiness verdict.
            The findings are kept on the record for inspection, but the run is
            never comparable: a truncated review reporting fewer findings is
            not evidence that a config found fewer issues.
    """

    OK = auto()
    INVALID_OUTPUT = auto()
    FAILED = auto()
    INCOMPLETE = auto()
