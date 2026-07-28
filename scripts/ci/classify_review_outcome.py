#!/usr/bin/env python3
"""Turn a ``lintro review`` run into an honest CI check outcome.

The dogfood AI review check reported ``success`` on every pull request while
producing no review at all: a depleted Anthropic balance made every run abort,
the wrapper swallowed the exit code, and ``AI Review ✓`` in the check list meant
nothing (#1826). This module is the decision point that fixes that — it maps a
``lintro review`` invocation to one of three outcomes:

* **reviewed** -- a review was produced (with or without P1 findings). Green.
* **not reviewed** -- no credential, a dead credential, a depleted balance, or an
  unreachable provider. The check goes red with a visible reason. It is
  deliberately *not* a required check, so a billing condition is loud without
  blocking a merge.
* **broken** -- lintro itself failed (bad flags, crash, unparseable output). Also
  red; the summary says so rather than blaming the provider.

The "not reviewed" branch is the No-Silent-Skip rule applied to the reviewer
itself: a check that could not do its job must never read as a pass.

Classification comes from the review error envelope
(:mod:`lintro.ai.review.error_contract`), so the taxonomy lives in lintro and is
not re-implemented here. Only the exit-code contract is local knowledge:

    0  reviewed, no P1 findings
    1  reviewed, P1 findings present
    2  review could not be produced (provider error or lintro-side failure)

Usage:
    scripts/ci/classify_review_outcome.py --status <n> --output-file <path>

Environment:
    GITHUB_STEP_SUMMARY  When set, the outcome is appended as Markdown.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from enum import StrEnum, auto
from pathlib import Path
from typing import Any, Final

# Exit statuses `lintro review` uses. Kept in sync with
# lintro.ai.review.error_contract.REVIEW_ERROR_EXIT_CODE and the
# has_p1_findings exit in lintro/cli_utils/commands/review.py;
# tests/scripts/test_run_ai_review.py fails if they drift.
REVIEW_STATUS_CLEAN: Final[int] = 0
REVIEW_STATUS_FINDINGS: Final[int] = 1
REVIEW_STATUS_ERROR: Final[int] = 2

# Sentinel used when the review never ran because no credential was present.
# The wrapper passes it instead of a real exit status so the missing-credential
# case lands in the same "not reviewed" branch as a dead one, rather than being
# special-cased into a silent skip.
NO_CREDENTIAL_STATUS: Final[int] = -1


class ReviewOutcome(StrEnum):
    """What actually happened to a review invocation.

    Members:
        REVIEWED: A review was produced; findings may or may not be present.
        NO_CREDENTIAL: No provider credential was available to review with.
        PROVIDER_UNAVAILABLE: The credential, balance, or endpoint failed.
        BROKEN: lintro itself could not complete the review.
    """

    REVIEWED = auto()
    NO_CREDENTIAL = auto()
    PROVIDER_UNAVAILABLE = auto()
    BROKEN = auto()

    @property
    def produced_review(self) -> bool:
        """Return whether a review actually reached the pull request.

        Returns:
            True only for :attr:`REVIEWED`.
        """
        return self is ReviewOutcome.REVIEWED


@dataclass(frozen=True, slots=True)
class OutcomeReport:
    """The classified outcome plus the copy CI should surface.

    Attributes:
        outcome: The classified outcome.
        headline: One-line status for the check summary and annotation.
        detail: Cause text from the provider, or an empty string.
        exit_code: Exit code the wrapper should terminate with.
    """

    outcome: ReviewOutcome
    headline: str
    detail: str
    exit_code: int


def _parse_error_envelope(*, text: str) -> dict[str, Any] | None:
    """Extract the ``error`` object from captured review output.

    The captured output interleaves lintro's logging with the JSON envelope, so
    the JSON is located rather than assumed to be the whole payload.

    Args:
        text: Combined stdout/stderr captured from the review run.

    Returns:
        The ``error`` mapping, or ``None`` when no envelope is present.
    """
    start = text.find('{\n  "error"')
    if start == -1:
        start = text.find('{"error"')
    if start == -1:
        return None
    decoder = json.JSONDecoder()
    try:
        payload, _end = decoder.raw_decode(text[start:])
    except ValueError:
        return None
    error = payload.get("error") if isinstance(payload, dict) else None
    return error if isinstance(error, dict) else None


def classify(*, status: int, output: str) -> OutcomeReport:
    """Classify a review invocation into a CI-facing outcome.

    Args:
        status: Exit status from ``lintro review``, or
            :data:`NO_CREDENTIAL_STATUS` when it was never invoked for want of a
            credential.
        output: Combined stdout/stderr captured from the run.

    Returns:
        The outcome, the copy to surface, and the exit code to terminate with.
    """
    if status == NO_CREDENTIAL_STATUS:
        return OutcomeReport(
            outcome=ReviewOutcome.NO_CREDENTIAL,
            headline="no provider credential — nothing was reviewed",
            detail=(
                "Add the ANTHROPIC_API_KEY repository secret to activate AI "
                "review on pull requests."
            ),
            exit_code=1,
        )

    if status in (REVIEW_STATUS_CLEAN, REVIEW_STATUS_FINDINGS):
        findings = status == REVIEW_STATUS_FINDINGS
        return OutcomeReport(
            outcome=ReviewOutcome.REVIEWED,
            headline=(
                "reviewed — P1 findings posted"
                if findings
                else "reviewed — no P1 findings"
            ),
            detail="",
            exit_code=0,
        )

    error = _parse_error_envelope(text=output) or {}
    kind = str(error.get("kind") or "unknown")
    message = str(error.get("message") or "").strip()

    if status != REVIEW_STATUS_ERROR:
        # An exit status lintro does not define means the wrapper itself broke
        # (missing dependency, bad flag, crash). Never attribute that to the
        # provider — the fix is in lintro, not in the account.
        return OutcomeReport(
            outcome=ReviewOutcome.BROKEN,
            headline=f"lintro review failed with unexpected status {status}",
            detail=message,
            exit_code=1,
        )

    if bool(error.get("provider_unavailable")):
        return OutcomeReport(
            outcome=ReviewOutcome.PROVIDER_UNAVAILABLE,
            headline=f"provider unavailable ({kind}) — nothing was reviewed",
            detail=message,
            exit_code=1,
        )

    return OutcomeReport(
        outcome=ReviewOutcome.BROKEN,
        headline=f"review could not complete ({kind}) — nothing was reviewed",
        detail=message,
        exit_code=1,
    )


def render_summary(*, report: OutcomeReport) -> str:
    """Render the outcome as a Markdown job-summary block.

    Args:
        report: The classified outcome.

    Returns:
        Markdown text ending in a newline.
    """
    icon = "✅" if report.outcome.produced_review else "🚫"
    lines = [f"### {icon} AI Review — {report.headline}", ""]
    if report.detail:
        lines.extend(["> " + report.detail, ""])
    if not report.outcome.produced_review:
        lines.extend(
            [
                "This check is informational and not required, so it cannot "
                "block a merge — but it is red because **no AI review was "
                "produced for this diff**. Treat the diff as un-reviewed by "
                "lintro and fall back to CodeRabbit/Greptile.",
                "",
            ],
        )
    return "\n".join(lines) + "\n"


def _emit(*, report: OutcomeReport) -> None:
    """Write the workflow annotation and job summary for an outcome.

    Args:
        report: The classified outcome.
    """
    annotation = "notice" if report.outcome.produced_review else "error"
    title = "AI Review"
    body = report.headline
    if report.detail:
        body = f"{body}: {report.detail}"
    # Newlines are not permitted inside a workflow command payload.
    print(f"::{annotation} title={title}::{body.replace(chr(10), ' ')}")

    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with Path(summary_path).open("a", encoding="utf-8") as handle:
            handle.write(render_summary(report=report))


def main(*, argv: list[str] | None = None) -> int:
    """Classify a review run and emit its CI-facing outcome.

    Args:
        argv: Optional argument vector (defaults to ``sys.argv[1:]``).

    Returns:
        Exit code for the wrapper: ``0`` when a review was produced, ``1``
        otherwise.
    """
    parser = argparse.ArgumentParser(description="Classify an AI review run.")
    parser.add_argument(
        "--status",
        type=int,
        required=True,
        help=(
            "Exit status from `lintro review`, or "
            f"{NO_CREDENTIAL_STATUS} when no credential was available."
        ),
    )
    parser.add_argument(
        "--output-file",
        default=None,
        help="File holding the captured review output (omit for none).",
    )
    args = parser.parse_args(argv)

    output = ""
    if args.output_file:
        path = Path(args.output_file)
        if path.exists():
            output = path.read_text(encoding="utf-8", errors="replace")

    report = classify(status=args.status, output=output)
    _emit(report=report)
    if not report.outcome.produced_review:
        print(f"AI Review: {report.headline}", file=sys.stderr)
    return report.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
