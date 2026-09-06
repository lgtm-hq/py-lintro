"""GitHub PR posting adapter for AI review results.

Renders a rich, telemetry-informative sticky comment (one per PR, updated in
place) with a severity-count header, TL;DR, per-finding blocks (severity color
emoji, category/confidence chips, visible reasoning), an always-visible
cumulative telemetry header, per-run mechanics with exact vs approximate (``~``)
labeling, and a machine-readable state block. All model-derived text is
sanitized (``@mentions`` neutralized, size capped) since it comes from an
untrusted PR diff.

Public helpers live in sibling modules and are re-exported here so existing
``lintro.ai.review.github`` imports remain stable after the size-gate split
(issue #1113).
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from typing import Any, Protocol

from loguru import logger

from lintro.ai.integrations.github_pr import GitHubPRReporter
from lintro.ai.review.enums.checklist_display import ChecklistDisplay
from lintro.ai.review.enums.finding_status import FindingStatus
from lintro.ai.review.enums.inline_post_failure_kind import InlinePostFailureKind
from lintro.ai.review.finding_matcher import (
    count_blocking_findings,
    fingerprint_for,
    match_findings,
    normalize_file_path,
)
from lintro.ai.review.github_constants import (
    ARCHIVE_MARKER,
    GITHUB_COMMENT_HARD_LIMIT,
    MAX_COMMENT_CHARS,
    STATE_MARKER_PREFIX,
    STICKY_MARKER,
)
from lintro.ai.review.github_errors import format_error_comment
from lintro.ai.review.github_lifecycle import (
    finding_marker,
    inline_comment_url,
    parse_finding_marker,
    regression_provenance,
    sync_addressed_lifecycle,
)
from lintro.ai.review.github_render import (
    REGRESSED_TITLE_SUFFIX,
    Section,
    _partition_findings,
    assemble,
    format_convergence_banner,
    format_finding_comment,
    format_inline_post_cause,
    format_run_mechanics,
    sanitize_comment_text,
)
from lintro.ai.review.github_review_body import build_review_body
from lintro.ai.review.inline_fix import (
    finding_suggested_change,
    normalize_diff_path,
    plan_inline_fix,
)
from lintro.ai.review.models.convergence_decision import ConvergenceDecision
from lintro.ai.review.models.finding_match_result import FindingMatchResult
from lintro.ai.review.models.finding_record import FindingRecord
from lintro.ai.review.models.inline_post_failure import InlinePostFailure
from lintro.ai.review.models.inline_post_result import InlinePostResult
from lintro.ai.review.models.review_finding import ReviewFinding
from lintro.ai.review.models.review_metadata import ReviewMetadata
from lintro.ai.review.models.review_result import ReviewResult
from lintro.ai.review.models.review_state import ReviewState
from lintro.ai.review.models.sticky_request import StickyRequest
from lintro.ai.review.output import render_inline_post_failure_json
from lintro.ai.review.sticky import (
    build_sticky_bodies,
    build_sticky_comment,
    matcher_reviewed_paths,
    parse_review_state,
    parse_review_state_v2,
    render_state_sticky,
)

__all__ = [
    "GITHUB_COMMENT_HARD_LIMIT",
    "STATE_MARKER_PREFIX",
    "STICKY_MARKER",
    "ARCHIVE_MARKER",
    "MAX_COMMENT_CHARS",
    "build_review_body",
    "build_sticky_comment",
    "format_error_comment",
    "format_finding_comment",
    "format_run_mechanics",
    "parse_review_state",
    "parse_review_state_v2",
    "post_review_converged_to_github",
    "post_review_error_to_github",
    "post_review_to_github",
    "sanitize_comment_text",
]


def post_review_to_github(
    *,
    result: ReviewResult,
    pr_number: int | None = None,
    repo: str | None = None,
    reporter: GitHubPRReporter | None = None,
    checklist_display: ChecklistDisplay = ChecklistDisplay.OFF,
    question_map: dict[int, str] | None = None,
    transport: str = "",
    auth_mode: str = "",
    cost_basis: str = "",
    config_source: str = "",
    auto_resolve: bool = True,
    prior_state: ReviewState | None = None,
    departed_paths: frozenset[str] | None = None,
    captured_comment_ids: dict[str, int] | None = None,
) -> bool:
    """Post (or update) the sticky review comment and inline findings.

    Maintains a single sticky comment per PR (identified by ``STICKY_MARKER``),
    updated in place with cumulative telemetry. Diff-mappable findings are also
    posted as inline review comments, under a review body describing this round
    (issue #1910). Threads whose finding this round settled are stamped with
    their outcome and, when configuration allows, resolved (issue #1912).

    Args:
        result: Review result to post.
        pr_number: Optional PR number override.
        repo: Optional repository override (owner/name).
        reporter: Optional preconfigured GitHub reporter.
        checklist_display: Structured checklist visibility mode.
        question_map: Prompt id to question text for linked display.
        transport: Provider transport used for this round (e.g. ``cli``).
        auth_mode: Authentication mode used by the transport.
        cost_basis: Provenance of the reported cost
            (``billed`` / ``estimated`` / ``unpriceable``).
        config_source: Human-readable description of where this run's settings
            came from, shown under the review body's run stats.
        auto_resolve: ``review.auto_resolve``. When false, an addressed thread
            still gets its banner but is left for a human to resolve.
        prior_state: Artifact or ledger state. When omitted, the sticky blob
            is migrated (findings and runs only).
        departed_paths: Paths that left the diff this round (deletes / rename
            sources). Their open findings may resolve.
        captured_comment_ids: Optional sink for newly captured inline comment
            ids so the caller can persist them after posting.

    Returns:
        True when posting succeeded; False on failure or when GitHub context is
        unavailable.
    """
    gh_reporter = reporter or GitHubPRReporter(pr_number=pr_number, repo=repo)
    if not gh_reporter.is_available():
        logger.warning("GitHub PR context not available — skipping review posting")
        return False

    prompt_questions = question_map or {}
    comment_id, sticky_state = _load_prior_state(reporter=gh_reporter)
    if prior_state is None or not (
        prior_state.coverage or prior_state.runs or prior_state.findings
    ):
        prior_state = sticky_state
    diff_lines = gh_reporter.fetch_pr_diff_lines()
    head_sha = result.metadata.head_ref

    def render(
        *,
        inline_failure: InlinePostFailure | None,
        comment_ids: dict[str, int] | None = None,
    ) -> str:
        """Render the primary sticky body against the unchanged prior state."""
        primary, archive = build_sticky_bodies(
            request=StickyRequest(
                result=result,
                prior_state=prior_state,
                head_sha=head_sha,
                checklist_display=checklist_display,
                question_map=prompt_questions,
                diff_lines=diff_lines,
                transport=transport,
                auth_mode=auth_mode,
                cost_basis=cost_basis,
                inline_failure=inline_failure,
                inline_comment_ids=comment_ids,
                repo=gh_reporter.repo or "",
                pr_number=gh_reporter.pr_number,
                departed_paths=departed_paths,
            ),
        )
        render.archive = archive  # type: ignore[attr-defined]
        return primary

    render.archive = None  # type: ignore[attr-defined]

    inline_findings, fallback = _partition_findings(
        findings=result.findings,
        diff_lines=diff_lines,
    )
    # Matching is pure and deterministic over (prior_state, findings), so the
    # sticky, the review body and the inline comments all recompute the same
    # transitions and cannot disagree about what is new or carried over.
    match = match_findings(
        previous=prior_state,
        findings=result.findings,
        round_number=prior_state.next_round,
        head_sha=head_sha,
        reviewed_paths=matcher_reviewed_paths(result=result),
        departed_paths=departed_paths,
    )

    # A finding that maps to no line in the diff never gets an inline comment,
    # so the sticky is its only surface from the outset — fold its detail in
    # rather than leaving it as a title in a table (#1909). The sticky is also
    # posted before the inline comments so an inline failure still leaves a
    # status comment on the PR.
    failure = _inline_failure(unmappable=fallback, rejected=[], outcome=None)
    # "Nothing to post" is not a failure, but it is also not a posted comment:
    # id capture has nothing to look for, while the lifecycle pass still runs
    # — a round that fixed everything posts no inline comment and yet has the
    # most threads to stamp.
    inline_posted = False
    success, comment_id = _upsert_sticky(
        reporter=gh_reporter,
        body=render(inline_failure=failure),
        comment_id=comment_id,
    )
    _upsert_archive(
        reporter=gh_reporter,
        body=getattr(render, "archive", None),
    )

    if inline_findings:
        # The body is self-contained — it carries its own fix prompt inline
        # (#1956) — so it needs nothing from the sticky upsert above and the
        # ordering here is driven only by the inline-failure fallback.
        review_body = build_review_body(
            result=result,
            prior_state=prior_state,
            match=match,
            head_sha=head_sha,
            transport=transport,
            auth_mode=auth_mode,
            config_source=config_source,
            new_commits=_count_new_commits(
                reporter=gh_reporter,
                prior_state=prior_state,
            ),
        )
        outcome = _post_inline_findings(
            reporter=gh_reporter,
            findings=inline_findings,
            checklist_display=checklist_display,
            question_map=prompt_questions,
            review_body=review_body,
            round_diff_lines=_round_diff_lines(
                reporter=gh_reporter,
                prior_state=prior_state,
                diff_lines=diff_lines,
                head_sha=head_sha,
            ),
            carried_fingerprints=frozenset(
                record.fingerprint for record in match.carried
            ),
            finding_keys=_record_keys(findings=inline_findings, match=match),
            provenance=_regression_provenance(reporter=gh_reporter, match=match),
        )
        inline_posted = outcome.ok
        if not inline_posted:
            success = False
            # Degraded path (#1909): the rejected findings now have no surface
            # either, so the sticky is re-rendered below with both groups
            # folded in. ``prior_state`` is unchanged, so this round is not
            # double-counted.
            failure = _inline_failure(
                unmappable=fallback,
                rejected=inline_findings,
                outcome=outcome,
            )
            if failure is not None:
                # The CI classifier reads this envelope out of the captured
                # log to report a sticky-only round honestly, instead of
                # claiming the findings were posted inline (#2266).
                logger.warning(
                    "Inline review comments were not posted; this round's "
                    "findings reached the sticky comment only: {}",
                    render_inline_post_failure_json(failure=failure),
                )

    comment_ids = _run_lifecycle(
        reporter=gh_reporter,
        match=match,
        prior_state=prior_state,
        head_sha=head_sha,
        round_number=prior_state.next_round,
        auto_resolve=auto_resolve,
        capture_ids=inline_posted,
    )

    # One follow-up edit at most: it carries the newly captured comment ids
    # (so a later round can find these threads) and, when inline posting
    # failed, the folded-in detail for findings that now have no surface.
    if comment_ids or (inline_findings and not inline_posted):
        _refresh_sticky(
            reporter=gh_reporter,
            render=render,
            failure=failure,
            comment_ids=comment_ids,
            comment_id=comment_id,
        )
        _upsert_archive(
            reporter=gh_reporter,
            body=getattr(render, "archive", None),
        )

    if captured_comment_ids is not None and comment_ids:
        captured_comment_ids.update(comment_ids)
    return success


def _inline_failure(
    *,
    unmappable: list[ReviewFinding],
    rejected: list[ReviewFinding],
    outcome: InlinePostResult | None,
) -> InlinePostFailure | None:
    """Describe the findings that have no inline comment to live on.

    Two different things put a finding here, and the reason says which: it
    anchors to no line in the PR's diff, or GitHub rejected the review batch
    that carried it. A rejection is classified from what GitHub actually
    answered, so a throttled token is never reported as a line-mapping
    problem (#2266).

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


class _StickyRenderer(Protocol):
    """Callable that renders the sticky body for a given inline-post outcome."""

    def __call__(
        self,
        *,
        inline_failure: InlinePostFailure | None,
        comment_ids: dict[str, int] | None = None,
    ) -> str:
        """Render the body.

        Args:
            inline_failure: Findings whose inline comments could not be posted.
            comment_ids: Finding key to inline comment id, persisted in the
                state blob so a later round can edit those comments.

        Returns:
            The complete sticky comment body.
        """
        ...  # pragma: no cover - structural type only


def _refresh_sticky(
    *,
    reporter: GitHubPRReporter,
    render: _StickyRenderer,
    failure: InlinePostFailure | None,
    comment_ids: dict[str, int],
    comment_id: int | None,
) -> None:
    """Re-render the sticky after this round's inline comments are known.

    Two things can only be written once the inline posting has been attempted:
    the ids of the comments it created (#1912) and, when it failed, the folded
    detail of findings that now have no surface (#1909). Failing here is not
    worth failing the run over — the caller has already recorded any inline
    failure — but it must not be silent either, or a PR quietly ends up with a
    verdict whose findings appear nowhere, or with threads no later round can
    find.

    Args:
        reporter: GitHub reporter used to locate and update the sticky comment.
        render: Callable that renders the sticky body.
        failure: Findings whose inline comments could not be posted.
        comment_ids: Finding key to inline comment id captured this round.
        comment_id: Live sticky comment id after the upsert, or ``None``
            when the comment was just created and must be re-located.
    """
    sticky_id = _sticky_comment_id(reporter=reporter, known=comment_id)
    if sticky_id is None:
        logger.warning(
            "Sticky comment not found — inline comment ids and any inline-post "
            "failure details could not be written to it",
        )
        return
    if not reporter.update_issue_comment(
        comment_id=sticky_id,
        body=render(inline_failure=failure, comment_ids=comment_ids),
    ):
        logger.warning("Failed to refresh the sticky comment after inline posting")


def _record_keys(
    *,
    findings: list[ReviewFinding],
    match: FindingMatchResult,
) -> list[str]:
    """Pair each posted finding with the identity key of its record.

    The key travels in a hidden marker on the inline comment, which is how the
    comment is recognized again when the PR's comments are listed back — the
    review-submission endpoint does not report the ids it created.

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


def _comment_url(*, reporter: GitHubPRReporter, comment_id: int | None) -> str:
    """Build the browser URL of an inline review comment.

    Args:
        reporter: GitHub reporter carrying repo and PR context.
        comment_id: Review comment id, or ``None`` when it is unknown.

    Returns:
        The comment's anchor URL, or an empty string — a pointer renders
        unlinked rather than as a dead link.
    """
    return inline_comment_url(
        repo=reporter.repo or "",
        pr_number=reporter.pr_number,
        comment_id=comment_id,
    )


def _regression_provenance(
    *,
    reporter: GitHubPRReporter,
    match: FindingMatchResult,
) -> dict[str, str]:
    """Build the provenance note each regression's fresh comment carries.

    Args:
        reporter: GitHub reporter used to link back to the original thread.
        match: This round's matching outcome.

    Returns:
        Finding key to the note. Regressions are re-raised on a new thread
        (state D), so without this a reader would be told a finding that was
        raised and fixed two rounds ago is simply new.
    """
    return {
        record.key: regression_provenance(
            record=record,
            thread_url=_comment_url(
                reporter=reporter,
                comment_id=record.inline_comment_id,
            ),
        )
        for record in match.regressed
    }


def _partial_progress(
    *,
    match: FindingMatchResult,
    prior_state: ReviewState,
) -> list[FindingRecord]:
    """Select collapsed patterns that lost occurrences this round (#1925).

    Args:
        match: This round's matching outcome.
        prior_state: State decoded from the sticky comment before this round.

    Returns:
        Open records whose addressed-occurrence count rose this round. A
        pattern that merely stayed where it was is excluded, so a long-lived
        finding is not re-stamped with the same banner every round.
    """
    before = {record.key: record for record in prior_state.findings}
    progressed: list[FindingRecord] = []
    for record in match.records:
        if record.status is not FindingStatus.OPEN:
            continue
        if record.inline_comment_id is None or record.occurrences_addressed <= 0:
            continue
        previous = before.get(record.key)
        if previous is not None and previous.occurrences_addressed >= (
            record.occurrences_addressed
        ):
            continue
        progressed.append(record)
    return progressed


def _fresh_thread_id(
    *,
    record: FindingRecord,
    newest: Mapping[str, int],
) -> int | None:
    """Return the comment id of a regression's *new* thread, when there is one.

    Args:
        record: The regressed record, still pointing at its old thread.
        newest: Highest comment id seen per finding key.

    Returns:
        The new comment's id, or ``None`` when this round posted no fresh
        comment for the finding — the old thread carries the same marker, so
        without this check its banner would link to itself.
    """
    candidate = newest.get(record.key)
    if candidate is None or candidate == record.inline_comment_id:
        return None
    return candidate


def _run_lifecycle(
    *,
    reporter: GitHubPRReporter,
    match: FindingMatchResult,
    prior_state: ReviewState,
    head_sha: str,
    round_number: int,
    auto_resolve: bool,
    capture_ids: bool,
) -> dict[str, int]:
    """Stamp settled threads and capture the ids of this round's comments.

    Both halves need the PR's inline comments, so the listing is fetched once
    and shared: the bodies are what the banner is applied to, and the hidden
    markers are what identifies a freshly posted comment.

    Args:
        reporter: GitHub reporter used for the listing, edits, and mutations.
        match: This round's matching outcome.
        prior_state: State decoded from the sticky comment before this round.
        head_sha: Head commit sha reviewed in this round.
        round_number: 1-based round number for this run.
        auto_resolve: Whether an addressed thread may also be resolved.
        capture_ids: Whether inline comments were posted this round, and so
            whether there are new ids to look for.

    Returns:
        Finding key to inline comment id for records that did not already have
        one (and for regressions, whose live thread is now the new one). Empty
        when there was nothing to capture or the listing failed.
    """
    partial = _partial_progress(match=match, prior_state=prior_state)
    regressed = tuple(
        record for record in match.regressed if record.inline_comment_id is not None
    )
    resolved = tuple(
        record for record in match.resolved if record.inline_comment_id is not None
    )
    if not capture_ids and not (partial or regressed or resolved):
        return {}

    comments = reporter.fetch_review_comments()
    if not isinstance(comments, list):
        logger.debug(
            "Could not list inline review comments — lifecycle banners and "
            "comment-id capture are skipped this round",
        )
        return {}

    bodies: dict[int, str] = {}
    newest: dict[str, int] = {}
    for comment in comments:
        comment_id = comment.get("id")
        body = comment.get("body")
        if not isinstance(comment_id, int) or not isinstance(body, str):
            continue
        bodies[comment_id] = body
        key = parse_finding_marker(body=body)
        if key:
            newest[key] = max(newest.get(key, 0), comment_id)

    sync_addressed_lifecycle(
        reporter=reporter,
        resolved=resolved,
        partial=partial,
        regressed=regressed,
        comment_bodies=bodies,
        head_sha=head_sha,
        round_number=round_number,
        auto_resolve=auto_resolve,
        new_thread_urls={
            record.key: _comment_url(
                reporter=reporter,
                comment_id=_fresh_thread_id(record=record, newest=newest),
            )
            for record in regressed
        },
    )

    if not capture_ids:
        return {}
    # A record that already has a thread keeps it: that is the comment the
    # banners are written onto. A regression is the exception — its live thread
    # is the fresh one, because the old thread stays resolved.
    regressed_keys = {record.key for record in match.regressed}
    return {
        record.key: newest[record.key]
        for record in match.records
        if record.key in newest
        and (record.inline_comment_id is None or record.key in regressed_keys)
    }


def _sticky_comment_id(
    *,
    reporter: GitHubPRReporter,
    known: int | None,
) -> int | None:
    """Return the sticky comment's id, re-locating it when it was just created.

    Args:
        reporter: GitHub reporter used to list PR comments.
        known: The live sticky id after upsert, or ``None`` when it was just
            created and must be re-located by marker.

    Returns:
        The comment id to update, or ``None`` when it still cannot be found.
    """
    if known is not None:
        return known
    found = reporter.find_issue_comment(marker=STICKY_MARKER)
    return None if found is None else found[0]


def _count_new_commits(
    *,
    reporter: GitHubPRReporter,
    prior_state: ReviewState,
) -> int | None:
    """Count commits pushed since the previously reviewed head.

    Args:
        reporter: GitHub reporter used to list the PR's commits.
        prior_state: State decoded from the sticky comment before this round.

    Returns:
        Number of commits after the previous round's head sha, or ``None`` when
        there is no previous round or the sha is not in the fetched listing.
    """
    if not prior_state.runs:
        return None
    prior_sha = prior_state.runs[-1].sha
    if not prior_sha:
        return None
    shas = reporter.fetch_pr_commit_shas()
    if not shas:
        return None
    for index, sha in enumerate(shas):
        if sha.startswith(prior_sha) or prior_sha.startswith(sha):
            return len(shas) - index - 1
    return None


def post_review_error_to_github(
    *,
    error: Exception,
    provider: str | None = None,
    metadata: ReviewMetadata | None = None,
    pr_number: int | None = None,
    repo: str | None = None,
    reporter: GitHubPRReporter | None = None,
    prior_state: ReviewState | None = None,
) -> bool:
    """Post (or update) the sticky comment with a formatted API-error message.

    When the sticky already carries a successful round, the failure is rendered
    as a banner over a re-render of that round's board rather than replacing it
    (#1954). The persisted state is passed through untouched either way, so a
    failed round never advances the round counter or edits tracked findings.

    Args:
        error: The exception raised during review.
        provider: Provider identifier used for provider-aware classification.
        metadata: Optional metadata for a mechanics footer.
        pr_number: Optional PR number override.
        repo: Optional repository override (owner/name).
        reporter: Optional preconfigured GitHub reporter.
        prior_state: Artifact or local ledger already loaded for this
            invocation. When set, the error sticky re-renders from it
            instead of decoding a leftover blob.

    Returns:
        True when posting succeeded; False otherwise.
    """
    gh_reporter = reporter or GitHubPRReporter(pr_number=pr_number, repo=repo)
    if not gh_reporter.is_available():
        logger.warning("GitHub PR context not available — skipping error posting")
        return False
    comment_id, sticky_state = _load_prior_state(reporter=gh_reporter)
    if prior_state is None or not (
        prior_state.coverage or prior_state.runs or prior_state.findings
    ):
        prior_state = sticky_state
    body = format_error_comment(
        error=error,
        provider=provider,
        metadata=metadata,
        prior_state=prior_state,
        repo=repo or gh_reporter.repo or "",
        pr_number=pr_number if pr_number is not None else gh_reporter.pr_number,
    )
    return _upsert_sticky(
        reporter=gh_reporter,
        body=body,
        comment_id=comment_id,
    )[0]


def post_review_converged_to_github(
    *,
    decision: ConvergenceDecision,
    pr_number: int | None = None,
    repo: str | None = None,
    reporter: GitHubPRReporter | None = None,
    prior_state: ReviewState | None = None,
) -> bool:
    """Stamp the sticky comment for a round the stop rule short-circuited.

    The board itself is re-rendered untouched from persisted state and the
    convergence banner is written under the header, exactly as a failed round
    is rendered (#1954): a skipped round produced no findings, so it must not
    advance the round counter, edit tracked findings, or blank the board a
    reviewer is still working from.

    Args:
        decision: The converged decision that skipped the round.
        pr_number: Optional PR number override.
        repo: Optional repository override (owner/name).
        reporter: Optional preconfigured GitHub reporter.
        prior_state: State already loaded for this invocation. When empty, the
            sticky's own decoded state is used instead.

    Returns:
        True when posting succeeded; False when there is no PR context or no
        recoverable prior state to re-render the board from.
    """
    gh_reporter = reporter or GitHubPRReporter(pr_number=pr_number, repo=repo)
    if not gh_reporter.is_available():
        logger.warning("GitHub PR context not available — skipping converged stamp")
        return False
    comment_id, sticky_state = _load_prior_state(reporter=gh_reporter)
    if prior_state is None or not (
        prior_state.coverage or prior_state.runs or prior_state.findings
    ):
        prior_state = sticky_state
    if not prior_state.runs:
        # Nothing recoverable to re-render: overwriting the live board with
        # the empty-state page would erase the findings a reviewer is still
        # working from, so leave the sticky untouched and say so.
        logger.warning(
            "No prior review state is recoverable — leaving the sticky "
            "untouched instead of stamping a converged round over it",
        )
        return False
    body = render_state_sticky(
        state=prior_state,
        banner=format_convergence_banner(
            decision=decision,
            open_p1=count_blocking_findings(findings=prior_state.findings),
        ),
        repo=repo or gh_reporter.repo or "",
        pr_number=pr_number if pr_number is not None else gh_reporter.pr_number,
    )
    return _upsert_sticky(
        reporter=gh_reporter,
        body=body,
        comment_id=comment_id,
    )[0]


def _load_prior_state(
    *,
    reporter: GitHubPRReporter,
) -> tuple[int | None, ReviewState]:
    """Locate the sticky comment and decode its persisted review state.

    Args:
        reporter: GitHub reporter used to list PR comments.

    Returns:
        Tuple of ``(comment_id, state)``; the id is ``None`` and the state is
        empty when no sticky comment exists yet.
    """
    found = reporter.find_issue_comment(marker=STICKY_MARKER)
    if found is None:
        return None, ReviewState()
    comment_id, prior_body = found
    return comment_id, parse_review_state_v2(body=prior_body)


def _upsert_archive(
    *,
    reporter: GitHubPRReporter,
    body: str | None,
) -> None:
    """Create or update the history-archive sticky when one was rendered.

    Args:
        reporter: GitHub reporter used to find and write the archive.
        body: Archive Markdown, or ``None`` when history still fits.
    """
    if not body:
        return
    found = reporter.find_issue_comment(marker=ARCHIVE_MARKER)
    comment_id = found[0] if found is not None else None
    _upsert_sticky(reporter=reporter, body=body, comment_id=comment_id)


def _upsert_sticky(
    *,
    reporter: GitHubPRReporter,
    body: str,
    comment_id: int | None,
) -> tuple[bool, int | None]:
    """Update the sticky comment in place, or create it when absent.

    GitHub only lets the creating actor PATCH a comment. After #2050 the
    poster is ``lintro-review[bot]``, so a leftover ``github-actions[bot]``
    sticky must be deleted and recreated rather than edited in place.

    Args:
        reporter: GitHub reporter used to create, edit, or replace the sticky.
        body: Markdown body to write.
        comment_id: Existing sticky id, or ``None`` when one has not been
            posted yet.

    Returns:
        ``(success, live_id)``. ``live_id`` is the comment later refreshes
        must PATCH: the original id after an in-place edit, the replacement
        id after a delete-and-recreate, or ``None`` after a first-time
        create (the caller re-locates by marker) or when the write failed.
    """
    if comment_id is None:
        return reporter.post_issue_comment(body), None
    status = _sticky_patch_status(
        reporter=reporter,
        comment_id=comment_id,
        body=body,
    )
    if status is not None and 200 <= status < 300:
        return True, comment_id
    if status != 403:
        logger.warning(
            "Could not edit sticky comment {} (HTTP {}); leaving it in place",
            comment_id,
            status,
        )
        return False, None
    logger.warning(
        "Could not edit sticky comment {}; posting a replacement "
        "before deleting it (GitHub only lets the creating actor PATCH)",
        comment_id,
    )
    live_id = _post_sticky(reporter=reporter, body=body)
    if live_id is None:
        return False, None
    if not reporter.delete_issue_comment(comment_id=comment_id):
        logger.warning(
            "Posted replacement sticky {} but failed to delete {}; "
            "both comments may remain",
            live_id,
            comment_id,
        )
    return True, live_id


def _sticky_patch_status(
    *,
    reporter: GitHubPRReporter,
    comment_id: int,
    body: str,
) -> int | None:
    """Return the sticky PATCH status, with a bool-reporter fallback.

    Args:
        reporter: GitHub reporter used to edit the sticky.
        comment_id: Existing sticky id.
        body: Markdown body to write.

    Returns:
        HTTP status when the reporter exposes one. Bool-only test doubles
        map success to ``200`` and failure to ``403`` so the actor-mismatch
        path stays covered without a status method.
    """
    status_fn = getattr(reporter, "update_issue_comment_status", None)
    if callable(status_fn):
        status = status_fn(comment_id=comment_id, body=body)
        if isinstance(status, int) or status is None:
            return status
    if reporter.update_issue_comment(comment_id=comment_id, body=body):
        return 200
    return 403


def _create_sticky_id(*, reporter: GitHubPRReporter, body: str) -> int | None:
    """Create a sticky comment and return its id.

    Args:
        reporter: GitHub reporter used to post the comment.
        body: Markdown body to write.

    Returns:
        The new comment id, or ``None`` when creation failed.
    """
    create_fn = getattr(reporter, "create_issue_comment", None)
    if callable(create_fn):
        created = create_fn(body=body)
        if isinstance(created, int) or created is None:
            return created
    if not reporter.post_issue_comment(body):
        return None
    found = reporter.find_issue_comment(marker=STICKY_MARKER)
    return None if found is None else found[0]


def _post_sticky(*, reporter: GitHubPRReporter, body: str) -> int | None:
    """Create the sticky comment, retrying once after a failed POST.

    Args:
        reporter: GitHub reporter used to post the comment.
        body: Markdown body to write.

    Returns:
        The new comment id, or ``None`` when both create attempts failed.
    """
    created = _create_sticky_id(reporter=reporter, body=body)
    if created is not None:
        return created
    logger.warning("Failed to recreate sticky comment; retrying once")
    return _create_sticky_id(reporter=reporter, body=body)


def _round_diff_lines(
    *,
    reporter: GitHubPRReporter,
    prior_state: ReviewState,
    diff_lines: dict[str, set[int]] | None,
    head_sha: str,
) -> dict[str, set[int]] | None:
    """Determine the lines this round's posted diff changed (#1911).

    A committable ``suggestion`` block is only valid where the review comment
    is anchored to a line this round posted. On round 1 that is the whole PR
    diff. Afterwards it is only what arrived since the previously reviewed
    head, so a finding sitting on untouched code loses its one-click fix even
    though the line is still inside the PR's cumulative diff.

    Args:
        reporter: GitHub reporter used to compare commits.
        prior_state: State decoded from the sticky comment before this round.
        diff_lines: The PR's cumulative diff lines.
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


def _post_inline_findings(
    *,
    reporter: GitHubPRReporter,
    findings: list[ReviewFinding],
    checklist_display: ChecklistDisplay,
    question_map: dict[int, str],
    review_body: str = "",
    round_diff_lines: dict[str, set[int]] | None = None,
    carried_fingerprints: frozenset[str] = frozenset(),
    finding_keys: Sequence[str] = (),
    provenance: Mapping[str, str] | None = None,
) -> InlinePostResult:
    """Post inline PR review comments for mappable findings.

    Each comment's fix slot is chosen by :func:`plan_inline_fix`. A comment in
    mode A is anchored to *exactly* the lines its suggestion replaces — a
    multi-line change carries ``start_line``/``line`` rather than a single
    ``line`` — because GitHub rejects a suggestion that does not cover its
    anchor exactly.

    Args:
        reporter: GitHub reporter used to submit the review.
        findings: Diff-mappable findings to anchor as inline comments.
        checklist_display: Structured checklist visibility mode.
        question_map: Prompt id to question text for linked display.
        review_body: Markdown body posted with the review. Falls back to a
            plain label when empty.
        round_diff_lines: Lines changed by this round's posted diff. ``None``
            disables committable suggestions entirely.
        carried_fingerprints: Fingerprints of findings already reported in an
            earlier round; they fall back to a described fix.
        finding_keys: Identity key per finding, in the same order, embedded as
            a hidden marker so the posted comment can be recognized later.
        provenance: Finding key to a provenance note prepended to its comment.
            A regression is re-raised on a fresh thread, and the note is what
            keeps it from reading as a brand-new finding.

    Returns:
        The submission outcome: whether GitHub accepted the review, and the
        status and message it answered with so the caller can say *why* a
        rejection happened (#2266).
    """
    notes = provenance or {}
    comments: list[dict[str, Any]] = []
    for index, finding in enumerate(findings):
        key = finding_keys[index] if index < len(finding_keys) else ""
        plan = plan_inline_fix(
            finding=finding,
            round_diff_lines=round_diff_lines,
            carried_over=fingerprint_for(
                file=finding.file,
                category=finding.category,
                title=finding.title,
            )
            in carried_fingerprints,
        )
        comment: dict[str, Any] = {
            "path": normalize_diff_path(finding.file),
            "body": _inline_body(
                body=format_finding_comment(
                    finding=finding,
                    checklist_display=checklist_display,
                    question_map=question_map,
                    inline_fix=plan,
                    # A regression's fresh thread must say so in its title:
                    # the provenance note explains the history, but the title
                    # is what a reader scanning the PR's comments sees.
                    title_suffix=REGRESSED_TITLE_SUFFIX if key in notes else "",
                ),
                key=key,
                note=notes.get(key, ""),
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
        comments.append(comment)

    attempted = tuple(finding_keys)
    if not comments:
        return InlinePostResult(ok=True, attempted_ids=attempted)

    payload = {
        "event": "COMMENT",
        "body": review_body or "Lintro review findings",
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
