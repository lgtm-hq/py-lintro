"""Fixed inputs for the GitHub comment goldens (issue #2303).

One review result and one prior state, both fully literal, so every snapshot
under ``snapshots/github/`` is reproducible from this module alone. They are
the behaviour baseline for #1974's convergence of the two posting paths: a
diff in those goldens means a comment a reviewer reads actually changed.

The shapes deliberately exercise everything the renderer branches on — a
blocking finding, a warning, a nit, a resolved finding carried from an earlier
round, two prior runs so the history table renders, and a checklist so the
appendix has something to fold.
"""

from __future__ import annotations

from lintro.ai.review.enums.finding_status import FindingStatus
from lintro.ai.review.enums.review_verdict import ReviewVerdict
from lintro.ai.review.finding_matcher import fingerprint_for, match_findings
from lintro.ai.review.models.checklist_answer import ChecklistAnswer
from lintro.ai.review.models.finding_match_result import FindingMatchResult
from lintro.ai.review.models.finding_record import FindingRecord
from lintro.ai.review.models.review_finding import ReviewFinding, Severity
from lintro.ai.review.models.review_metadata import ReviewMetadata
from lintro.ai.review.models.review_result import ReviewResult
from lintro.ai.review.models.review_state import ReviewState
from lintro.ai.review.models.run_record import RunRecord
from lintro.ai.review.sticky import matcher_reviewed_paths

#: Head commit of the round the goldens render.
GOLDEN_HEAD_SHA: str = "0d15ea5edeadbeef0d15ea5edeadbeef0d15ea5e"

#: Head commit of the round already recorded in the prior state.
GOLDEN_PRIOR_SHA: str = "1111111aaaaaaaa1111111aaaaaaaa1111111aaa"

#: Version the review body's run-stats table renders. Pinned so a release
#: bump does not rewrite a golden that says nothing about review behaviour.
GOLDEN_LINTRO_VERSION: str = "0.147.6"

#: Repository slug and PR number used to link finding titles to their threads.
GOLDEN_REPO: str = "lgtm-hq/py-lintro"
GOLDEN_PR_NUMBER: int = 2303

#: Identity of the P1 the fixture round re-reports. Derived the way production
#: derives it, so the matcher actually carries the record across rounds and the
#: goldens cover the carried-finding layout rather than a first sighting.
_CARRIED_FINGERPRINT: str = fingerprint_for(
    file="src/auth/session.py",
    category="security",
    title="Unknown session status grants access",
)

#: Identity of a finding the fixture's second round already resolved. Nothing
#: in the current round re-reports it, so it only has to be stable.
_RESOLVED_FINGERPRINT: str = fingerprint_for(
    file="src/auth/tokens.py",
    category="correctness",
    title="Token refresh drops the expiry",
)


def golden_review_result() -> ReviewResult:
    """Build the fixed review result the comment goldens render.

    Returns:
        ReviewResult: A three-finding round with a checklist and metadata
        covering every badge the sticky renders.
    """
    return ReviewResult(
        metadata=ReviewMetadata(
            model="claude-sonnet-4-20250514",
            provider="anthropic",
            transport="api",
            auth_mode="api-key",
            context_window=200_000,
            depth=2,
            strictness="balanced",
            chunks_total=2,
            chunks_current=2,
            files_reviewed=3,
            files_total=3,
            checklist_items=2,
            token_usage={"prompt": 12_000, "completion": 2_400, "total": 14_400},
            cost_estimate_usd=0.1234,
            cost_basis="estimated",
            base_ref="main",
            head_ref="feature/contract",
            timestamp="2026-09-05T10:00:00+00:00",
            duration_seconds=42.5,
        ),
        summary="Two blocking issues remain; the session fix landed.",
        checklist=(
            ChecklistAnswer(
                id=1,
                answer="no",
                evidence="src/auth/session.py:31",
                question="Does an unknown status fail closed?",
            ),
            ChecklistAnswer(
                id=2,
                answer="yes",
                evidence="tests/test_session.py:12",
                question="Are the access paths covered by tests?",
            ),
        ),
        findings=(
            ReviewFinding(
                severity=Severity.P1,
                category="security",
                file="src/auth/session.py",
                line=31,
                title="Unknown session status grants access",
                description="The else branch treats any unrecognised status "
                "as active.",
                cause="No explicit default for unknown statuses.",
                fix="Default to expired and log the unrecognised value.",
                confidence="high",
                checklist_ids=(1,),
            ),
            ReviewFinding(
                severity=Severity.P2,
                category="test-gap",
                file="tests/test_session.py",
                line=12,
                title="No test covers the expired branch",
                description="The expired path is never exercised.",
                cause="Test gap.",
                fix="Add a case asserting the expired status is rejected.",
                confidence="medium",
                checklist_ids=(2,),
            ),
            ReviewFinding(
                severity=Severity.P3,
                category="style",
                file="src/auth/tokens.py",
                line=7,
                title="Docstring omits the raised error",
                description="The Raises section is missing.",
                cause="Oversight.",
                fix="Document the ValueError.",
                confidence="low",
            ),
        ),
    )


def golden_prior_state() -> ReviewState:
    """Build the fixed prior state the comment goldens carry into the round.

    Returns:
        ReviewState: Two recorded runs, one carried open finding, and one
        finding already resolved in the second round.
    """
    return ReviewState(
        runs=(
            RunRecord(
                round=1,
                timestamp="2026-09-03T09:00:00+00:00",
                sha=GOLDEN_PRIOR_SHA,
                model="claude-sonnet-4-20250514",
                provider="anthropic",
                transport="api",
                auth_mode="api-key",
                cost_basis="estimated",
                depth=2,
                strictness="balanced",
                files_reviewed=3,
                checks=2,
                duration=38.0,
                prompt=11_000,
                completion=2_100,
                total=13_100,
                cost=0.1101,
                verdict=ReviewVerdict.BLOCKED,
                open_after=2,
            ),
            RunRecord(
                round=2,
                timestamp="2026-09-04T09:00:00+00:00",
                sha=GOLDEN_PRIOR_SHA,
                model="claude-sonnet-4-20250514",
                provider="anthropic",
                transport="api",
                auth_mode="api-key",
                cost_basis="estimated",
                depth=2,
                strictness="balanced",
                files_reviewed=3,
                checks=2,
                duration=40.0,
                prompt=11_500,
                completion=2_200,
                total=13_700,
                cost=0.1150,
                verdict=ReviewVerdict.BLOCKED,
                resolved=1,
                open_after=1,
            ),
        ),
        findings=(
            FindingRecord(
                fingerprint=_CARRIED_FINGERPRINT,
                severity=Severity.P1,
                category="security",
                title="Unknown session status grants access",
                file="src/auth/session.py",
                line=31,
                status=FindingStatus.OPEN,
                since_round=1,
                checklist_ids=(1,),
            ),
            FindingRecord(
                fingerprint=_RESOLVED_FINGERPRINT,
                severity=Severity.P2,
                category="correctness",
                title="Token refresh drops the expiry",
                file="src/auth/tokens.py",
                line=44,
                status=FindingStatus.RESOLVED,
                since_round=1,
                resolved_round=2,
                resolved_sha=GOLDEN_PRIOR_SHA,
            ),
        ),
        repo=GOLDEN_REPO,
        pr_number=GOLDEN_PR_NUMBER,
        head_sha=GOLDEN_PRIOR_SHA,
    )


def golden_match() -> FindingMatchResult:
    """Match this round's findings against the pinned prior state.

    Derived with the production matcher rather than hand-built, so the review
    body golden covers the carried-finding and resolved-delta wording the
    matcher actually produces.

    Returns:
        FindingMatchResult: The round's matching outcome.
    """
    result = golden_review_result()
    prior = golden_prior_state()
    return match_findings(
        previous=prior,
        findings=result.findings,
        round_number=prior.next_round,
        head_sha=GOLDEN_HEAD_SHA,
        reviewed_paths=matcher_reviewed_paths(result=result),
    )


def golden_first_round_match() -> FindingMatchResult:
    """Match this round's findings against an empty prior state.

    A first round still runs the matcher — every finding comes back as new —
    so the golden covers what the posting path actually hands the renderer
    rather than a hand-built empty result.

    Returns:
        FindingMatchResult: The first round's matching outcome.
    """
    result = golden_review_result()
    return match_findings(
        previous=ReviewState(),
        findings=result.findings,
        round_number=1,
        head_sha=GOLDEN_HEAD_SHA,
        reviewed_paths=matcher_reviewed_paths(result=result),
    )
