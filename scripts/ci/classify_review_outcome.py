#!/usr/bin/env python3
r"""Turn a ``lintro review`` run into an honest CI check outcome.

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

Transport-aware refinement (#1923): shared outcomes keep their names; API-only
and CLI-only failure vocabularies are distinguished so a subscription-CLI kill
is never misread as "no credits". Every headline names the transport.

Usage:
    scripts/ci/classify_review_outcome.py --status <n> --output-file <path> \
        [--transport api|cli]

Environment:
    GITHUB_STEP_SUMMARY  When set, the outcome is appended as Markdown.
"""

from __future__ import annotations

import argparse
import json
import os
import re
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

# Sentinel for every other way the review never got invoked -- no PR number, a
# failed config patch, a broken setup step. These used to abort the wrapper under
# `set -e`, which reddened the check but produced no annotation and no summary: a
# red check that does not say why is only marginally better than a green one.
NOT_INVOKED_STATUS: Final[int] = -2

# 128 + SIGTERM. The wrapper's ``wait`` reports this when the runner signals
# the step even if ``lintro review`` already wrote a persist envelope and
# exited 0. Treat that envelope as the outcome, not "unexpected status 143".
SIGTERM_STATUS: Final[int] = 143

DEFAULT_TRANSPORT: Final[str] = "cli"

# Kind labels refined for the active transport. Shared kinds stay as-is;
# transport-specific labels make CI summaries self-diagnosing (#1923).
_API_KIND_LABELS: Final[dict[str, str]] = {
    "insufficient_credits": "insufficient_credits",
    "auth_failed": "auth_failed:key",
}
_CLI_KIND_LABELS: Final[dict[str, str]] = {
    "auth_failed": "auth_failed:oauth_session",
    "timeout": "turn_timeout",
}

# These patterns classify unstructured CLI subprocess prose — when the CLI
# fails before lintro can emit its JSON error envelope, stderr wording is all
# there is. They were validated against claude CLI 2.1.x (2026-08); if the
# binary rewrites an error message, the affected class silently degrades to
# the generic kind label from _CLI_KIND_LABELS, so revalidate these patterns
# whenever the pinned claude CLI version moves.
_CLI_VERSION_DRIFT = re.compile(
    r"cli.?version|unsupported.+claude|json-schema-name|unknown option|"
    r"unrecognized arguments",
    re.IGNORECASE,
)
_KILLED_EXTERNALLY = re.compile(
    r"killed|signal\s*9|sigkill|runner.*(cancel|shut)|job timed out|"
    r"The operation was canceled|cancelled by",
    re.IGNORECASE,
)
_OAUTH_AUTH = re.compile(
    r"not logged in|run /login|oauth|CLAUDE_CODE_OAUTH|session.*(expir|invalid)",
    re.IGNORECASE,
)
_API_KEY_AUTH = re.compile(
    r"api[- ]?key|x-api-key|authentication_error|invalid.+key",
    re.IGNORECASE,
)


class ReviewOutcome(StrEnum):
    """What actually happened to a review invocation.

    Members:
        REVIEWED: A review was produced; findings may or may not be present.
        INCOMPLETE: A review was produced but coverage-at-HEAD is not 100%.
        NO_CREDENTIAL: No provider credential was available to review with.
        PROVIDER_UNAVAILABLE: The credential, balance, or endpoint failed.
        BROKEN: lintro itself could not complete the review.
    """

    REVIEWED = auto()
    INCOMPLETE = auto()
    NO_CREDENTIAL = auto()
    PROVIDER_UNAVAILABLE = auto()
    BROKEN = auto()

    @property
    def produced_review(self) -> bool:
        """Return whether a review actually reached the pull request.

        Returns:
            True for :attr:`REVIEWED` and :attr:`INCOMPLETE` (a partial
            review was produced).
        """
        return self in {ReviewOutcome.REVIEWED, ReviewOutcome.INCOMPLETE}


@dataclass(frozen=True, slots=True)
class OutcomeReport:
    """The classified outcome plus the copy CI should surface.

    Attributes:
        outcome: The classified outcome.
        headline: One-line status for the check summary and annotation.
        detail: Cause text from the provider, or an empty string.
        exit_code: Exit code the wrapper should terminate with.
        transport: Transport named on every outcome line.
    """

    outcome: ReviewOutcome
    headline: str
    detail: str
    exit_code: int
    transport: str = DEFAULT_TRANSPORT


def _parse_coverage_envelope(*, text: str) -> dict[str, Any] | None:
    """Extract the coverage object from a successful review JSON envelope.

    Args:
        text: Combined stdout/stderr captured from the review run.

    Returns:
        The coverage mapping, or ``None`` when absent.
    """
    decoder = json.JSONDecoder()
    index = text.find("{")
    while index != -1:
        try:
            payload, _end = decoder.raw_decode(text[index:])
        except ValueError:
            index = text.find("{", index + 1)
            continue
        if isinstance(payload, dict) and "readiness_verdict" in payload:
            coverage = payload.get("coverage")
            if isinstance(coverage, dict):
                if "stopped_reason" not in coverage and payload.get("stopped_reason"):
                    coverage = {
                        **coverage,
                        "stopped_reason": payload.get("stopped_reason"),
                    }
                return coverage
            if payload.get("readiness_verdict") == "incomplete":
                return {
                    "complete": False,
                    "covered_at_head": 0,
                    "eligible": 0,
                    "stopped_reason": payload.get("stopped_reason") or "",
                }
        index = text.find("{", index + 1)
    return None


def _parse_error_envelope(*, text: str) -> dict[str, Any] | None:
    """Extract the ``error`` object from captured review output.

    The captured output interleaves lintro's logging with the JSON envelope, so
    the JSON is located rather than assumed to be the whole payload.

    Args:
        text: Combined stdout/stderr captured from the review run.

    Returns:
        The ``error`` mapping, or ``None`` when no envelope is present.
    """
    decoder = json.JSONDecoder()
    index = text.find("{")
    while index != -1:
        try:
            payload, _end = decoder.raw_decode(text[index:])
        except ValueError:
            index = text.find("{", index + 1)
            continue
        error = payload.get("error") if isinstance(payload, dict) else None
        if isinstance(error, dict):
            return error
        index = text.find("{", index + 1)
    return None


def _normalize_transport(transport: str) -> str:
    """Normalize a transport label to ``api`` or ``cli``.

    Args:
        transport: Raw transport string from the CLI or env.

    Returns:
        Lowercased transport; unknown values fall back to ``cli`` (dogfood).
    """
    normalized = (transport or DEFAULT_TRANSPORT).strip().lower()
    if normalized in {"api", "cli"}:
        return normalized
    return DEFAULT_TRANSPORT


def _with_transport(*, transport: str, headline: str) -> str:
    """Prefix a headline with the transport name.

    Args:
        transport: Active transport.
        headline: Outcome headline without transport.

    Returns:
        Headline that always names the transport.
    """
    return f"[{transport}] {headline}"


def _incomplete_report(
    *,
    coverage: dict[str, Any],
    transport: str,
) -> OutcomeReport:
    """Build the INCOMPLETE outcome from a parsed coverage envelope.

    Args:
        coverage: Coverage mapping from the review JSON envelope.
        transport: Active transport named on the headline.

    Returns:
        Report that reddens the check and tells the next round to resume.
    """
    covered = coverage.get("covered_at_head", 0)
    eligible = coverage.get("eligible", 0)
    return OutcomeReport(
        outcome=ReviewOutcome.INCOMPLETE,
        headline=_with_transport(
            transport=transport,
            headline=(
                "review incomplete — "
                f"{covered}/{eligible} files covered at HEAD; "
                "next round resumes"
            ),
        ),
        detail=str(coverage.get("stopped_reason") or ""),
        exit_code=1,
        transport=transport,
    )


def _reviewed_report(*, findings: bool, transport: str) -> OutcomeReport:
    """Build the REVIEWED outcome for a finished envelope.

    Args:
        findings: True when the review posted P1 findings.
        transport: Active transport named on the headline.

    Returns:
        Green report; the review itself produced a result.
    """
    return OutcomeReport(
        outcome=ReviewOutcome.REVIEWED,
        headline=_with_transport(
            transport=transport,
            headline=(
                "reviewed — P1 findings posted"
                if findings
                else "reviewed — no P1 findings"
            ),
        ),
        detail="",
        exit_code=0,
        transport=transport,
    )


def refine_failure_kind(
    *,
    transport: str,
    kind: str,
    message: str,
    output: str,
) -> str:
    """Map a canonical error kind onto the transport's failure vocabulary.

    Args:
        transport: Active transport (``api`` or ``cli``).
        kind: Canonical kind from the review error envelope.
        message: Envelope message text.
        output: Full captured output (used when the envelope is thin).

    Returns:
        A transport-aware kind label for CI summaries.
    """
    haystack = f"{message}\n{output}"
    if transport == "cli":
        # Prose patterns may only classify a *thin* envelope (no concrete
        # kind): a real envelope kind (insufficient_credits, timeout, ...)
        # must keep its own label even when interleaved logs mention
        # "killed", version drift, or the OAuth session. An envelope's
        # existence also means lintro finished writing it — a run the
        # runner actually killed leaves no kind to override.
        thin_envelope = kind in ("", "unknown")
        if thin_envelope and _KILLED_EXTERNALLY.search(haystack):
            return "killed_externally"
        if thin_envelope and _CLI_VERSION_DRIFT.search(haystack):
            return "cli_version_drift"
        if kind == "auth_failed":
            if _API_KEY_AUTH.search(haystack) and not _OAUTH_AUTH.search(haystack):
                return "auth_failed:key"
            return "auth_failed:oauth_session"
        if thin_envelope and _OAUTH_AUTH.search(haystack):
            return "auth_failed:oauth_session"
        return _CLI_KIND_LABELS.get(kind, kind)

    if kind == "auth_failed":
        return "auth_failed:key"
    return _API_KIND_LABELS.get(kind, kind)


def classify(
    *,
    status: int,
    output: str,
    reason: str = "",
    transport: str = DEFAULT_TRANSPORT,
) -> OutcomeReport:
    """Classify a review invocation into a CI-facing outcome.

    Args:
        status: Exit status from ``lintro review``, or one of
            :data:`NO_CREDENTIAL_STATUS` / :data:`NOT_INVOKED_STATUS` when the
            review was never reached.
        output: Combined stdout/stderr captured from the run.
        reason: Wrapper-supplied explanation for a never-invoked run.
        transport: Active transport (``api`` or ``cli``); named on every line.

    Returns:
        The outcome, the copy to surface, and the exit code to terminate with.
    """
    transport = _normalize_transport(transport)

    if status == NOT_INVOKED_STATUS:
        return OutcomeReport(
            outcome=ReviewOutcome.BROKEN,
            headline=_with_transport(
                transport=transport,
                headline="the review was never invoked — nothing was reviewed",
            ),
            detail=reason or output.strip()[-500:],
            exit_code=1,
            transport=transport,
        )

    if status == NO_CREDENTIAL_STATUS:
        detail = (
            "Add the CLAUDE_CODE_OAUTH_TOKEN secret to activate AI review "
            "on pull requests — the dogfood runs the `cli` transport, "
            "which authenticates through the `claude` CLI's OAuth session."
            if transport == "cli"
            else (
                "Add the ANTHROPIC_API_KEY (or provider-equivalent) secret "
                "to activate AI review on the `api` transport."
            )
        )
        return OutcomeReport(
            outcome=ReviewOutcome.NO_CREDENTIAL,
            headline=_with_transport(
                transport=transport,
                headline="no provider credential — nothing was reviewed",
            ),
            detail=detail,
            exit_code=1,
            transport=transport,
        )

    # A persist envelope wins over the wrapper exit status. ``wait`` reports
    # 143 when the runner SIGTERMs the step after lintro already wrote
    # INCOMPLETE JSON and exited 0 (#2156 / #2166 round 5).
    coverage = _parse_coverage_envelope(text=output)
    if coverage is not None and not coverage.get("complete", True):
        return _incomplete_report(coverage=coverage, transport=transport)

    if status in (REVIEW_STATUS_CLEAN, REVIEW_STATUS_FINDINGS):
        return _reviewed_report(
            findings=status == REVIEW_STATUS_FINDINGS,
            transport=transport,
        )

    error = _parse_error_envelope(text=output) or {}
    kind = str(error.get("kind") or "unknown")
    message = str(error.get("message") or "").strip()
    if not message and output.strip():
        # No envelope means lintro itself broke (crash, bad flag, missing
        # dependency) rather than the provider failing. Fall back to the tail of
        # the raw output so the annotation and summary are never blank — an empty
        # reason is how a red check still fails to explain itself.
        message = output.strip().splitlines()[-1][:500]

    refined_kind = refine_failure_kind(
        transport=transport,
        kind=kind,
        message=message,
        output=output,
    )

    if status != REVIEW_STATUS_ERROR:
        # ``wait`` can report SIGTERM after a finished review already wrote a
        # complete envelope. Prefer that over "unexpected status 143".
        if coverage is not None and coverage.get("complete", True):
            return _reviewed_report(findings=False, transport=transport)
        # An exit status lintro does not define means the wrapper itself broke
        # (missing dependency, bad flag, crash). Never attribute that to the
        # provider — the fix is in lintro, not in the account.
        return OutcomeReport(
            outcome=ReviewOutcome.BROKEN,
            headline=_with_transport(
                transport=transport,
                headline=f"lintro review failed with unexpected status {status}",
            ),
            detail=message,
            exit_code=1,
            transport=transport,
        )

    if refined_kind in {"killed_externally", "cli_version_drift", "turn_timeout"}:
        return OutcomeReport(
            outcome=ReviewOutcome.BROKEN,
            headline=_with_transport(
                transport=transport,
                headline=(
                    f"review could not complete ({refined_kind}) — nothing was reviewed"
                ),
            ),
            detail=message,
            exit_code=1,
            transport=transport,
        )

    if bool(error.get("provider_unavailable")):
        return OutcomeReport(
            outcome=ReviewOutcome.PROVIDER_UNAVAILABLE,
            headline=_with_transport(
                transport=transport,
                headline=(
                    f"provider unavailable ({refined_kind}) — nothing was reviewed"
                ),
            ),
            detail=message,
            exit_code=1,
            transport=transport,
        )

    return OutcomeReport(
        outcome=ReviewOutcome.BROKEN,
        headline=_with_transport(
            transport=transport,
            headline=(
                f"review could not complete ({refined_kind}) — nothing was reviewed"
            ),
        ),
        detail=message,
        exit_code=1,
        transport=transport,
    )


def render_summary(*, report: OutcomeReport) -> str:
    """Render the outcome as a Markdown job-summary block.

    Args:
        report: The classified outcome.

    Returns:
        Markdown text ending in a newline.
    """
    if report.outcome is ReviewOutcome.INCOMPLETE:
        icon = "⚠️"
    elif report.outcome.produced_review:
        icon = "✅"
    else:
        icon = "🚫"
    lines = [
        f"### {icon} AI Review ({report.transport}) — {report.headline}",
        "",
    ]
    if report.outcome is ReviewOutcome.INCOMPLETE:
        lines.extend(
            [
                "A review was produced, but coverage-at-HEAD is not 100%. "
                "The next round resumes with unreviewed files first. "
                "P1 findings still pass this check; an unfinished review "
                "does not.",
                "",
            ],
        )
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
    title = f"AI Review ({report.transport})"
    body = report.headline
    if report.detail:
        body = f"{body}: {report.detail}"
    # Workflow-command payloads need `%`, CR and LF percent-encoded, in that
    # order -- escaping `%` last would re-escape the escapes.
    escaped = body.replace("%", "%25").replace("\r", "%0D").replace("\n", " ")
    print(f"::{annotation} title={title}::{escaped}")

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
            "Exit status from `lintro review`; "
            f"{NO_CREDENTIAL_STATUS} when no credential was available, "
            f"{NOT_INVOKED_STATUS} when it was never invoked at all."
        ),
    )
    parser.add_argument(
        "--output-file",
        default=None,
        help="File holding the captured review output (omit for none).",
    )
    parser.add_argument(
        "--reason",
        default="",
        help=(
            "Why the review was never invoked; surfaced as the outcome detail "
            f"when --status is {NOT_INVOKED_STATUS}."
        ),
    )
    parser.add_argument(
        "--transport",
        default=DEFAULT_TRANSPORT,
        choices=("api", "cli"),
        help=(
            "Transport used for the review (default: cli). Named on every "
            "annotation and job-summary line; selects the failure vocabulary."
        ),
    )
    args = parser.parse_args(argv)

    output = ""
    if args.output_file:
        path = Path(args.output_file)
        if path.exists():
            output = path.read_text(encoding="utf-8", errors="replace")

    report = classify(
        status=args.status,
        output=output,
        reason=args.reason,
        transport=args.transport,
    )
    _emit(report=report)
    if not report.outcome.produced_review:
        print(f"AI Review: {report.headline}", file=sys.stderr)
    return report.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
