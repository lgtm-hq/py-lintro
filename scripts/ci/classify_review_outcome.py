#!/usr/bin/env python3
r"""Turn a ``lintro review`` run into an honest CI check outcome.

The dogfood AI review check reported ``success`` on every pull request while
producing no review at all: a depleted Anthropic balance made every run abort,
the wrapper swallowed the exit code, and ``AI Review ✓`` in the check list meant
nothing (#1826). This module is the decision point that fixes that — it maps a
``lintro review`` invocation to one of these outcomes:

* **reviewed** -- a review was produced (with or without P1 findings). Green.
* **degraded** -- a review was produced and posted, and every eligible file was
  reviewed, but not at full depth: the envelope's
  ``findings_coverage_complete`` is false, so a per-call findings cap, an
  output-exhaustion retry, an incomplete cross-chunk synthesis pass, or a
  failed depth-2/3 pass may have suppressed findings (#2395). The findings are
  kept; the check goes red and the annotation names every recorded reason,
  because a partial finding set must never read as a clean pass.
* **converged** -- the deterministic convergence stop rule (#2099) skipped the
  round before any provider call, because the last N rounds all scored below
  the configured threshold. Nothing was reviewed, but nothing needed to be, and
  the reason is stated rather than implied by a silent pass. Green, including
  when the last real round left open P1 findings: a REVIEWED round reports P1s
  without reddening (see the exit-code contract below), and a skipped round is
  not stricter about the same findings than the round that found them. The
  count is never hidden, though -- ``open_p1`` stays on the envelope and the
  headline says "skipped: N open P1 findings remain".
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

The classifier's own exit code is separate: 0 only when a complete review (or
a deliberate convergence skip) answered the question, and 1 for every outcome
where it did not -- including a review that was produced but is incomplete on
the file axis or degraded on the depth axis.

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
from collections.abc import Iterator, Mapping
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

# Key lintro logs the inline-post failure envelope under. Kept in sync with
# lintro.ai.review.output.INLINE_POST_FAILURE_KEY; this script runs from a
# bare python3 on the runner and cannot import lintro.
INLINE_POST_FAILURE_KEY: Final[str] = "inline_post_failure"

DEFAULT_TRANSPORT: Final[str] = "cli"

# Top-level key `lintro review` writes when the convergence stop rule skipped
# the round (#2099). Mirrors lintro.ai.review.output.CONVERGED_ENVELOPE_KEY;
# tests/scripts/test_classify_review_outcome.py fails if the two drift.
CONVERGED_ENVELOPE_KEY: Final[str] = "converged"

# Value of the envelope's top-level `outcome` field for a skipped round. Used
# as the discriminator so a nested object carrying a `converged` key can never
# be mistaken for the envelope itself. Mirrors
# lintro.ai.review.output.CONVERGED_OUTCOME; a contract test pins the pair.
CONVERGED_OUTCOME: Final[str] = "converged"

# Top-level keys `lintro review` writes for the finding-depth axis (#2003 /
# #2395). Mirror lintro.ai.review.output.review_result_to_dict; a contract
# test in tests/scripts/test_classify_review_outcome.py pins the names.
# ``findings_coverage_complete`` is false whenever the run recorded any
# coverage degradation -- a per-call findings cap, an output-exhaustion retry,
# an incomplete cross-chunk synthesis pass, or a depth-2/3 pass that failed
# and left the chunk on its main-pass result.
DEPTH_COMPLETE_KEY: Final[str] = "findings_coverage_complete"
DEPTH_DEGRADATIONS_KEY: Final[str] = "coverage_degradations"

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
        DEGRADED: Every eligible file was reviewed, but not at full depth --
            the envelope's ``findings_coverage_complete`` is false (#2395).
        CONVERGED: The round was deliberately skipped by the convergence stop
            rule before any provider call (#2099).
        NO_CREDENTIAL: No provider credential was available to review with.
        PROVIDER_UNAVAILABLE: The credential, balance, or endpoint failed.
        BROKEN: lintro itself could not complete the review.
    """

    REVIEWED = auto()
    INCOMPLETE = auto()
    DEGRADED = auto()
    CONVERGED = auto()
    NO_CREDENTIAL = auto()
    PROVIDER_UNAVAILABLE = auto()
    BROKEN = auto()

    @property
    def produced_review(self) -> bool:
        """Return whether a review actually reached the pull request.

        Returns:
            True for :attr:`REVIEWED`, :attr:`INCOMPLETE` and
            :attr:`DEGRADED` (a partial review was produced).
        """
        return self in {
            ReviewOutcome.REVIEWED,
            ReviewOutcome.INCOMPLETE,
            ReviewOutcome.DEGRADED,
        }

    @property
    def partial_review(self) -> bool:
        """Return whether a review reached the PR but is not a full one.

        Both members here posted their findings and both exit non-zero: the
        check must never read as a clean pass over a diff the reviewer only
        partly covered. :attr:`INCOMPLETE` is the file axis (some files were
        never reviewed) and :attr:`DEGRADED` the depth axis (every file was
        reviewed, but a limit or a failed pass may have suppressed findings).

        Returns:
            True for :attr:`INCOMPLETE` and :attr:`DEGRADED`.
        """
        return self in {ReviewOutcome.INCOMPLETE, ReviewOutcome.DEGRADED}

    @property
    def review_unavailable(self) -> bool:
        """Return whether the diff went un-reviewed for a bad reason.

        A skipped-because-converged round produced no review either, but it
        is a decision rather than a failure: it must not carry the
        "treat the diff as un-reviewed, fall back to CodeRabbit/Greptile"
        advice.

        This controls that fallback copy and the ``::error`` annotation only
        — it is not the readiness gate. There is no readiness gate at check
        level: open P1 findings are reported and never reddened, on a
        REVIEWED round and on a CONVERGED skip alike. Both name the count and
        exit 0; the merge decision is the reviewer's, not this check's.

        Returns:
            True only for the outcomes where a review was wanted and could
            not be produced.
        """
        return self in {
            ReviewOutcome.NO_CREDENTIAL,
            ReviewOutcome.PROVIDER_UNAVAILABLE,
            ReviewOutcome.BROKEN,
        }


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


def _payload_has_p1_findings(payload: Mapping[str, Any]) -> bool:
    """Return whether a review envelope lists any P1 finding.

    Questions are excluded even when the model labelled one P1, matching
    ``ReviewResult.has_p1_findings`` and ``derive_verdict`` on the lintro
    side: an open question is a request for information, not a defect claim,
    and the two P1 gates must not disagree about what blocks.

    Args:
        payload: Decoded review JSON object.

    Returns:
        True when any non-question finding severity is ``P1``.
    """
    findings = payload.get("findings")
    if not isinstance(findings, list):
        return False
    for item in findings:
        if not isinstance(item, Mapping):
            continue
        if str(item.get("kind") or "").lower().endswith("question"):
            continue
        severity = str(item.get("severity") or "").upper()
        if severity in {"P1", "SEVERITY.P1"}:
            return True
    return False


def _iter_json_objects(*, text: str) -> Iterator[dict[str, Any]]:
    """Yield every JSON object embedded in captured review output.

    The captured output interleaves lintro's logging with one or more JSON
    envelopes, so each ``{`` is tried as a document start and the objects
    that decode are yielded in order. The single scan shared by every
    envelope parser below, so a fix here applies to all of them.

    Args:
        text: Combined stdout/stderr captured from the review run.

    Yields:
        dict[str, Any]: Each top-level JSON object found in ``text``.
    """
    decoder = json.JSONDecoder()
    index = text.find("{")
    while index != -1:
        try:
            payload, _end = decoder.raw_decode(text[index:])
        except ValueError:
            index = text.find("{", index + 1)
            continue
        if isinstance(payload, dict):
            yield payload
        index = text.find("{", index + 1)


def _parse_coverage_envelope(*, text: str) -> dict[str, Any] | None:
    """Extract the coverage object from a successful review JSON envelope.

    Args:
        text: Combined stdout/stderr captured from the review run.

    Returns:
        The coverage mapping, or ``None`` when absent.
    """
    for payload in _iter_json_objects(text=text):
        if "readiness_verdict" not in payload:
            continue
        coverage = payload.get("coverage")
        extras = {
            "stopped_reason": payload.get("stopped_reason") or "",
            "has_p1_findings": _payload_has_p1_findings(payload),
        }
        if isinstance(coverage, dict):
            merged = {**coverage}
            if not merged.get("stopped_reason") and extras["stopped_reason"]:
                merged["stopped_reason"] = extras["stopped_reason"]
            merged["has_p1_findings"] = extras["has_p1_findings"]
            return merged
        if payload.get("readiness_verdict") == "incomplete":
            return {
                "complete": False,
                "covered_at_head": 0,
                "eligible": 0,
                **extras,
            }
    return None


def _parse_degraded_envelope(*, text: str) -> dict[str, Any] | None:
    """Extract the finding-depth block from a review JSON envelope.

    Keyed off ``readiness_verdict`` like :func:`_parse_coverage_envelope`, so
    a nested object carrying the same field names can never be mistaken for
    the envelope. A run that reported full depth, and an older envelope that
    predates the key, both return ``None``: absence is never degradation.

    Args:
        text: Combined stdout/stderr captured from the review run.

    Returns:
        A mapping with the recorded degradation reasons, or ``None`` when the
        run's finding depth was complete.
    """
    for payload in _iter_json_objects(text=text):
        if "readiness_verdict" not in payload:
            continue
        if payload.get(DEPTH_COMPLETE_KEY, True):
            continue
        raw = payload.get(DEPTH_DEGRADATIONS_KEY)
        reasons: list[str] = []
        if isinstance(raw, list):
            for item in raw:
                if not isinstance(item, Mapping):
                    continue
                reason = str(item.get("reason") or "").strip()
                if reason and reason not in reasons:
                    reasons.append(reason)
        return {
            "reasons": reasons,
            "has_p1_findings": _payload_has_p1_findings(payload),
        }
    return None


def _degraded_report(
    *,
    status: int,
    output: str,
    transport: str,
) -> OutcomeReport | None:
    """Build the DEGRADED outcome for a review that ran at reduced depth.

    The review itself is kept: its findings are already posted to the PR and
    the sticky comment. What changes is the verdict this check reports --
    ``findings_coverage_complete == false`` means the finding set is not a
    guaranteed full one, so the check must not read as a clean pass (#2395).

    Being red must not cost the reader anything a green round would have
    told them, so the headline carries the same findings news
    :func:`_reviewed_report` renders -- the P1 count, or the inline-post
    failure that sent the findings to the sticky comment alone -- and the
    detail carries that failure's reason alongside the degradation reasons.

    Nothing is reported at :data:`REVIEW_STATUS_ERROR`, for the same reason
    the converged branch stands down there: something broke after the
    envelope was printed, and that failure is the news.

    Args:
        status: Exit status from ``lintro review``.
        output: Combined stdout/stderr captured from the run.
        transport: Active transport named on the headline.

    Returns:
        Report that reddens the check, names the findings news and every
        recorded reason, or ``None`` when the finding depth was complete.
    """
    if status == REVIEW_STATUS_ERROR:
        return None
    degraded = _parse_degraded_envelope(text=output)
    if degraded is None:
        return None
    reasons = [str(reason) for reason in degraded.get("reasons") or []]
    named = ", ".join(reasons) if reasons else "reason not recorded"
    inline_failure = _parse_inline_post_failure(text=output)
    news = _findings_news(
        findings=bool(degraded.get("has_p1_findings")),
        inline_failure=inline_failure,
    )
    detail = f"Coverage degradations: {named}."
    inline_reason = (
        str(inline_failure.get("reason") or "") if inline_failure is not None else ""
    )
    if inline_reason:
        detail = f"{detail} {inline_reason}"
    return OutcomeReport(
        outcome=ReviewOutcome.DEGRADED,
        headline=_with_transport(
            transport=transport,
            headline=(
                f"partial review — {news}; finding depth was limited, "
                "not a guaranteed full finding set"
            ),
        ),
        detail=detail,
        exit_code=1,
        transport=transport,
    )


def _parse_inline_post_failure(*, text: str) -> dict[str, Any] | None:
    """Extract the inline-post failure envelope from captured review output.

    ``lintro review --post`` logs this envelope when GitHub refused the
    inline review batch, which means the round's findings reached the sticky
    comment only. Without it the summary claimed the findings were posted
    (#2266).

    Args:
        text: Combined stdout/stderr captured from the review run.

    Returns:
        The failure mapping, or ``None`` when inline posting was fine.
    """
    for payload in _iter_json_objects(text=text):
        failure = payload.get(INLINE_POST_FAILURE_KEY)
        if isinstance(failure, dict):
            return failure
    return None


def _parse_converged_envelope(*, text: str) -> dict[str, Any] | None:
    """Extract the convergence stop-rule object from captured review output.

    Shares :func:`_iter_json_objects` with every other envelope parser, so a
    later fix to the scan reaches the stop-rule branch too. That scan tries
    every ``{``, so it also yields objects nested inside a larger payload:
    the ``outcome`` discriminator is therefore required alongside the
    ``converged`` mapping, and only the producer's own top-level envelope
    carries both. A finding or coverage object that merely happens to hold a
    ``converged`` key can no longer classify a real review as a skip.

    Args:
        text: Combined stdout/stderr captured from the review run.

    Returns:
        The ``converged`` mapping, or ``None`` when the round was not skipped.
    """
    for payload in _iter_json_objects(text=text):
        if payload.get("outcome") != CONVERGED_OUTCOME:
            continue
        converged = payload.get(CONVERGED_ENVELOPE_KEY)
        if isinstance(converged, dict):
            return {**converged, "detail": str(payload.get("detail") or "")}
    return None


def _converged_open_p1(*, converged: dict[str, Any]) -> int | None:
    """Read the open-P1 count off a converged envelope, or report it unusable.

    A numeric string or a whole-valued float is accepted — those are shapes a
    JSON producer can legitimately emit for a count. A boolean, a fraction, a
    negative, a missing key, or anything else is not a count, and is reported
    as unusable rather than silently degraded to zero.

    Args:
        converged: The ``converged`` mapping from the review JSON envelope.

    Returns:
        The count, or ``None`` when the field cannot be read as one.
    """
    raw = converged.get("open_p1")
    if isinstance(raw, bool) or raw is None:
        return None
    if isinstance(raw, int):
        return raw if raw >= 0 else None
    if isinstance(raw, float):
        return int(raw) if raw.is_integer() and raw >= 0 else None
    if isinstance(raw, str):
        try:
            parsed = int(raw.strip())
        except ValueError:
            return None
        return parsed if parsed >= 0 else None
    return None


def _converged_report(
    *,
    converged: dict[str, Any],
    transport: str,
) -> OutcomeReport:
    """Build the CONVERGED outcome from a parsed stop-rule envelope.

    Args:
        converged: The ``converged`` mapping from the review JSON envelope.
        transport: Active transport named on the headline.

    Returns:
        Green report naming the round that was skipped, why, and how many
        open P1 findings the last real round left behind.
    """
    round_number = converged.get("round", 0)
    stable_rounds = converged.get("stable_rounds", 0)
    open_p1 = _converged_open_p1(converged=converged)
    if open_p1 is None:
        # The count is the whole readiness gate for a skipped round. An
        # unreadable one cannot be assumed to mean "nothing blocking": that
        # would turn a malformed envelope into a green check, which is the
        # silent pass this module exists to prevent. Fail closed and say so.
        return OutcomeReport(
            outcome=ReviewOutcome.BROKEN,
            headline=_with_transport(
                transport=transport,
                headline=(
                    "converged envelope is unreadable — open_p1 is "
                    f"{converged.get('open_p1')!r}, not a count"
                ),
            ),
            detail=(
                "lintro wrote a convergence stop-rule envelope whose open_p1 "
                "field is missing or not a non-negative integer, so the "
                "readiness gate for the skipped round cannot be evaluated."
            ),
            exit_code=1,
            transport=transport,
        )
    noun = "finding" if open_p1 == 1 else "findings"
    remaining = f"; skipped: {open_p1} open P1 {noun} remain" if open_p1 > 0 else ""
    return OutcomeReport(
        outcome=ReviewOutcome.CONVERGED,
        headline=_with_transport(
            transport=transport,
            headline=(
                f"converged — round {round_number} skipped after "
                f"{stable_rounds} stable rounds{remaining}"
            ),
        ),
        detail=str(converged.get("detail") or ""),
        # Exit 0 even with open P1s, mirroring a REVIEWED round: this check
        # reports P1 findings without reddening for them (see the exit-code
        # contract in scripts/ci/run-ai-review.sh), and a skipped round must
        # not be stricter about the same findings than the round that found
        # them. The readiness gate is informational at check level on both
        # paths, so the count is named in the headline instead of hidden
        # behind an exit code.
        exit_code=0,
        transport=transport,
    )


def _parse_error_envelope(*, text: str) -> dict[str, Any] | None:
    """Extract the ``error`` object from captured review output.

    The captured output interleaves lintro's logging with the JSON envelope, so
    the JSON is located rather than assumed to be the whole payload.

    Args:
        text: Combined stdout/stderr captured from the review run.

    Returns:
        The ``error`` mapping, or ``None`` when no envelope is present.
    """
    for payload in _iter_json_objects(text=text):
        error = payload.get("error")
        if isinstance(error, dict):
            return error
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


def _findings_news(
    *,
    findings: bool,
    inline_failure: Mapping[str, Any] | None,
) -> str:
    """Describe what happened to this round's findings.

    Shared by :func:`_reviewed_report` and :func:`_degraded_report` so a
    degraded round reports the same findings news as a full one; without it
    the depth-axis headline silently dropped the P1 count and the
    inline-post failure a complete review would have named (#2395).

    Args:
        findings: True when the review posted P1 findings.
        inline_failure: Inline-post failure envelope, or ``None`` when the
            inline comments went up normally.

    Returns:
        A fragment naming where the findings went, or whether there were any.
    """
    if inline_failure is not None:
        kind = str(inline_failure.get("kind") or "unknown")
        return f"findings posted to the sticky comment only ({kind})"
    return "P1 findings posted" if findings else "no P1 findings"


def _reviewed_report(
    *,
    findings: bool,
    transport: str,
    inline_failure: Mapping[str, Any] | None = None,
) -> OutcomeReport:
    """Build the REVIEWED outcome for a finished envelope.

    A round GitHub refused the inline comments for still produced a review, so
    it stays green with an unchanged exit code — but it must not claim the
    findings were posted inline when they only reached the sticky comment
    (#2266).

    Args:
        findings: True when the review posted P1 findings.
        transport: Active transport named on the headline.
        inline_failure: Inline-post failure envelope, or ``None`` when the
            inline comments went up normally.

    Returns:
        Green report; the review itself produced a result.
    """
    news = _findings_news(findings=findings, inline_failure=inline_failure)
    return OutcomeReport(
        outcome=ReviewOutcome.REVIEWED,
        headline=_with_transport(transport=transport, headline=f"reviewed — {news}"),
        detail=(
            str(inline_failure.get("reason") or "")
            if inline_failure is not None
            else ""
        ),
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

    # A converged round is checked early: it produces no coverage and no
    # error envelope at all, so every later branch would have to guess at a
    # review that deliberately never ran.
    #
    # Not, however, ahead of a hard failure. The stop rule exits 0 or 1 and
    # never 2, so status 2 alongside a converged envelope means something
    # broke *after* the envelope was printed — a failed sticky post, a
    # crashing later step. The failure is the news; reporting the skip would
    # bury it behind a green-looking outcome, which is exactly the silent
    # pass this module exists to prevent. Let the error branches below own
    # that case.
    if status != REVIEW_STATUS_ERROR:
        converged = _parse_converged_envelope(text=output)
        if converged is not None:
            return _converged_report(converged=converged, transport=transport)

    # A persist envelope wins over the wrapper exit status. ``wait`` reports
    # 143 when the runner SIGTERMs the step after lintro already wrote
    # INCOMPLETE JSON and exited 0 (#2156 / #2166 round 5).
    coverage = _parse_coverage_envelope(text=output)
    if coverage is not None and not coverage.get("complete", True):
        return _incomplete_report(coverage=coverage, transport=transport)

    # Depth degradation is checked after the file axis: a run that left files
    # unreviewed *and* ran short on depth is reported as INCOMPLETE, because
    # resuming the missing files is the actionable news. Both exit 1, so the
    # check is red either way.
    degraded = _degraded_report(status=status, output=output, transport=transport)
    if degraded is not None:
        return degraded

    if status in (REVIEW_STATUS_CLEAN, REVIEW_STATUS_FINDINGS):
        return _reviewed_report(
            findings=status == REVIEW_STATUS_FINDINGS,
            transport=transport,
            inline_failure=_parse_inline_post_failure(text=output),
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
            return _reviewed_report(
                findings=bool(coverage.get("has_p1_findings")),
                transport=transport,
            )
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
    if report.outcome.partial_review:
        icon = "⚠️"
    elif report.outcome is ReviewOutcome.CONVERGED:
        icon = "🔁"
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
    if report.outcome is ReviewOutcome.DEGRADED:
        lines.extend(
            [
                "Every eligible file was reviewed, but not at full depth: the "
                "run recorded a coverage degradation, so findings may have "
                "gone unreported. The review and its findings are posted as "
                "usual — this check is red because the finding set is not a "
                "guaranteed complete one.",
                "",
            ],
        )
    if report.outcome is ReviewOutcome.CONVERGED:
        lines.extend(
            [
                "No provider call was made: the convergence stop rule found "
                "the open findings stable below the configured threshold, so "
                "another round would have re-reported the same set. Re-run "
                "the review with `--full` to force one.",
                "",
            ],
        )
    if report.detail:
        lines.extend(["> " + report.detail, ""])
    if report.outcome.review_unavailable:
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
    if report.outcome.review_unavailable:
        annotation = "error"
    elif report.outcome is ReviewOutcome.DEGRADED:
        # A warning, not a notice: the review is on the PR, but the check is
        # red and the annotation must say why at a glance (#2395).
        annotation = "warning"
    else:
        annotation = "notice"
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
        Exit code for the wrapper. ``0`` means the review question was
        answered: a complete review ran, or the convergence stop rule
        deliberately skipped the round. Open P1 findings do not change that on
        either path — they are reported in the headline and summary, never
        reddened, because this check is informational and not required. ``1``
        means either that no review was produced at all (no credential, dead
        credential, depleted balance, unreachable provider, a lintro-side
        failure, or an unreadable envelope) or that the review that *was*
        produced is not a full one — files left uncovered at HEAD, or a
        finding depth the run itself recorded as degraded (#2395). Exit ``0``
        is therefore not a promise that a review ran, and exit ``1`` is never
        about findings.
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
    if report.outcome.review_unavailable:
        print(f"AI Review: {report.headline}", file=sys.stderr)
    return report.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
