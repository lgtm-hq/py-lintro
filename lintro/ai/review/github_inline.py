"""Posting a round's findings as inline pull-request review comments (#1910).

A finding that anchors to a line in the diff is reported where the code is,
not in a table. This module owns that submission — one review, one API call,
every mappable finding under it — and the descriptor the sticky board reads
when a finding could not get an inline surface at all.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from loguru import logger

from lintro.ai.integrations.github_pr import GitHubPRReporter
from lintro.ai.review.enums.finding_status import FindingStatus
from lintro.ai.review.enums.inline_post_failure_kind import InlinePostFailureKind
from lintro.ai.review.finding_matcher import fingerprint_for, normalize_file_path
from lintro.ai.review.github_notes import format_inline_post_cause
from lintro.ai.review.github_render import (
    REGRESSED_TITLE_SUFFIX,
    Section,
    assemble,
    format_finding_comment,
)
from lintro.ai.review.inline_fix import (
    finding_suggested_change,
    normalize_diff_path,
    plan_inline_fix,
)
from lintro.ai.review.lifecycle.markers import finding_marker
from lintro.ai.review.models.finding_match_result import FindingMatchResult
from lintro.ai.review.models.inline_post_failure import InlinePostFailure
from lintro.ai.review.models.inline_post_request import InlinePostRequest
from lintro.ai.review.models.inline_post_result import InlinePostResult
from lintro.ai.review.models.review_finding import ReviewFinding
from lintro.ai.review.models.review_state import ReviewState

__all__ = [
    "describe_inline_failure",
    "post_inline_findings",
    "record_keys",
    "round_diff_lines",
]


def describe_inline_failure(
    *,
    unmappable: list[ReviewFinding],
    rejected: list[ReviewFinding],
    outcome: InlinePostResult | None,
) -> InlinePostFailure | None:
    """Describe the findings that have no inline comment to live on.

    Two different things put a finding here, and the reason says which: it
    anchors to no line in the pull request's diff, or GitHub rejected the
    review batch that carried it. A rejection is classified from what GitHub
    actually answered, so a throttled token is never reported as a
    line-mapping problem (#2266).

    Args:
        unmappable: Findings that map to no line in the diff.
        rejected: Findings whose inline review batch the API rejected.
        outcome: Result of the rejected POST, or ``None`` when nothing was
            submitted.

    Returns:
        The failure descriptor, or ``None`` when every finding has an inline
        surface.
    """
    findings = [*rejected, *unmappable]
    if not findings:
        return None
    kind = InlinePostFailureKind.LINE_MAPPING
    status: int | None = None
    reasons: list[str] = []
    if rejected:
        answered = outcome or InlinePostResult(ok=False)
        status = answered.status
        kind = InlinePostFailureKind.from_response(
            status=status,
            message=answered.message,
        )
        reasons.append(format_inline_post_cause(kind=kind, status=status))
    # Unmappable findings were never submitted, so they carry no status of
    # their own — and a rejection that was itself a line-mapping one already
    # said this, so the reason says it once rather than twice.
    said_already = bool(rejected) and kind is InlinePostFailureKind.LINE_MAPPING
    if unmappable and not said_already:
        reasons.append(
            format_inline_post_cause(kind=InlinePostFailureKind.LINE_MAPPING),
        )
    return InlinePostFailure(
        reason="; ".join(reasons),
        findings=tuple(findings),
        kind=kind,
        status=status,
    )


def record_keys(
    *,
    findings: list[ReviewFinding],
    match: FindingMatchResult,
) -> list[str]:
    """Pair each posted finding with the identity key of its record.

    The key travels in a hidden marker on the inline comment, which is how the
    comment is recognized again when the pull request's comments are listed
    back — the review-submission endpoint does not report the ids it created.

    Args:
        findings: Findings being posted inline, in posting order.
        match: This round's matching outcome, whose records carry the keys.

    Returns:
        One key per finding, in the same order. An entry is empty when no
        record could be paired, which leaves that comment unmarked rather than
        mislabeled as another finding's thread.
    """
    slots: dict[tuple[str, str, int], list[str]] = defaultdict(list)
    for record in match.records:
        if record.status is FindingStatus.OPEN:
            slots[(record.fingerprint, record.file, record.line)].append(record.key)

    keys: list[str] = []
    for finding in findings:
        slot = (
            fingerprint_for(
                file=finding.file,
                category=finding.category,
                title=finding.title,
            ),
            normalize_file_path(finding.file),
            finding.line,
        )
        bucket = slots.get(slot)
        keys.append(bucket.pop(0) if bucket else "")
    return keys


def round_diff_lines(
    *,
    reporter: GitHubPRReporter,
    prior_state: ReviewState,
    diff_lines: dict[str, set[int]] | None,
    head_sha: str,
) -> dict[str, set[int]] | None:
    """Determine the lines this round's posted diff changed (#1911).

    A committable ``suggestion`` block is only valid where the review comment
    is anchored to a line this round posted. On round 1 that is the whole pull
    request diff. Afterwards it is only what arrived since the previously
    reviewed head, so a finding sitting on untouched code loses its one-click
    fix even though the line is still inside the cumulative diff.

    Args:
        reporter: GitHub reporter used to compare commits.
        prior_state: State carried into this round.
        diff_lines: The pull request's cumulative diff lines.
        head_sha: This round's head commit sha.

    Returns:
        Lines changed by this round, or ``None`` when the round's diff cannot
        be established — every suggestion then falls back to a described fix
        rather than risking a suggestion GitHub will reject.
    """
    if not prior_state.runs:
        return diff_lines
    prior_sha = prior_state.runs[-1].sha
    if not prior_sha or not head_sha:
        return None
    return reporter.fetch_compare_lines(base=prior_sha, head=head_sha)


def _inline_body(*, body: str, key: str, note: str) -> str:
    """Assemble an inline comment body with provenance and identity marker.

    Args:
        body: Rendered finding comment.
        key: Identity key of the finding's record, possibly empty.
        note: Provenance note for a regression, possibly empty.

    Returns:
        The body to post. The marker is hidden (an HTML comment) and sits last,
        so it never affects how the comment reads.
    """
    # ``budget=None``: an inline comment carries no prunable section, and it
    # has never been capped. Capping it here would be a behaviour change in an
    # overflow path this issue does not own; the assembly is what converges.
    return assemble(
        sections=[
            Section(name="provenance", text=note),
            Section(name="finding", text=body),
            Section(name="marker", text=finding_marker(key=key)),
        ],
        budget=None,
    )


def _comment_payload(
    *,
    finding: ReviewFinding,
    key: str,
    request: InlinePostRequest,
) -> dict[str, Any]:
    """Build the API payload for one finding's inline comment.

    Args:
        finding: The finding to anchor.
        key: Identity key of the finding's record, possibly empty.
        request: The batch this comment belongs to.

    Returns:
        dict[str, Any]: One entry of the review submission's ``comments``
        list. A comment in fix mode A is anchored to *exactly* the lines its
        suggestion replaces, because GitHub rejects a suggestion that does not
        cover its anchor exactly.
    """
    plan = plan_inline_fix(
        finding=finding,
        round_diff_lines=request.round_diff_lines,
        carried_over=fingerprint_for(
            file=finding.file,
            category=finding.category,
            title=finding.title,
        )
        in request.carried_fingerprints,
    )
    comment: dict[str, Any] = {
        "path": normalize_diff_path(finding.file),
        "body": _inline_body(
            body=format_finding_comment(
                finding=finding,
                checklist_display=request.checklist_display,
                question_map=dict(request.question_map),
                inline_fix=plan,
                # A regression's fresh thread must say so in its title: the
                # provenance note explains the history, but the title is what
                # a reader scanning the pull request's comments sees.
                title_suffix=(
                    REGRESSED_TITLE_SUFFIX if key in request.provenance else ""
                ),
            ),
            key=key,
            note=request.provenance.get(key, ""),
        ),
        "line": finding.line,
        "side": "RIGHT",
    }
    change = plan.committable_change
    if change is not None and change.is_multiline:
        comment["start_line"] = change.start_line
        comment["start_side"] = "RIGHT"
        comment["line"] = change.end_line
    elif plan.rejection is not None and finding_suggested_change(finding=finding):
        # The model did offer a hunk and it was refused. Say which rule
        # refused it: a silently described fix looks like the model simply
        # had nothing mechanical to propose.
        logger.debug(
            "No committable suggestion for {}:{} — {}",
            finding.file,
            finding.line,
            plan.rejection.value,
        )
    return comment


def post_inline_findings(
    *,
    reporter: GitHubPRReporter,
    request: InlinePostRequest,
) -> InlinePostResult:
    """Post inline pull-request review comments for mappable findings.

    Args:
        reporter: GitHub reporter used to submit the review.
        request: The findings to anchor and everything their comments render.

    Returns:
        The submission outcome: whether GitHub accepted the review, and the
        status and message it answered with so the caller can say *why* a
        rejection happened (#2266).
    """
    comments: list[dict[str, Any]] = []
    for index, finding in enumerate(request.findings):
        key = request.finding_keys[index] if index < len(request.finding_keys) else ""
        comments.append(
            _comment_payload(finding=finding, key=key, request=request),
        )

    attempted = tuple(request.finding_keys)
    if not comments:
        return InlinePostResult(ok=True, attempted_ids=attempted)

    payload = {
        "event": "COMMENT",
        "body": request.review_body or "Lintro review findings",
        "comments": comments,
    }
    url = (
        f"{reporter.api_base}/repos/{reporter.repo}/pulls/{reporter.pr_number}/reviews"
    )
    response = reporter.api_response("POST", url, payload)
    return InlinePostResult(
        ok=response.ok,
        status=response.status,
        message=response.message,
        attempted_ids=attempted,
    )
