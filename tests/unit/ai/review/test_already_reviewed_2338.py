"""The already-reviewed guard: a covered head makes no provider call (#2338).

The guard fails toward review: every condition it cannot establish from the
state runs a round. These tests pin the one shape that skips and each shape
that must not.
"""

from __future__ import annotations

from dataclasses import replace

import pytest
from assertpy import assert_that

from lintro.ai.review.already_reviewed import (
    FORCE_ENV,
    AlreadyReviewed,
    evaluate_already_reviewed,
    force_requested,
    publication_complete,
)
from lintro.ai.review.enums.coverage_degradation_reason import (
    CoverageDegradationReason,
)
from lintro.ai.review.enums.finding_kind import FindingKind
from lintro.ai.review.enums.finding_status import FindingStatus
from lintro.ai.review.github_constants import STICKY_MARKER
from lintro.ai.review.models.changed_file import ChangedFile
from lintro.ai.review.models.coverage_degradation import CoverageDegradation
from lintro.ai.review.models.coverage_record import CoverageRecord
from lintro.ai.review.models.degradation_record import DegradationRecord
from lintro.ai.review.models.finding_record import FindingRecord
from lintro.ai.review.models.flagged_file import FlaggedFile
from lintro.ai.review.models.review_context import ReviewContext
from lintro.ai.review.models.review_finding import Severity
from lintro.ai.review.models.review_result import ReviewResult
from lintro.ai.review.models.review_state import ReviewState
from lintro.ai.review.models.run_coverage import RunCoverage
from lintro.ai.review.models.run_identity import RunIdentity
from lintro.ai.review.models.run_record import RunRecord
from lintro.ai.review.models.sticky_request import StickyRequest
from lintro.ai.review.patch_hash import normalized_patch_hash
from lintro.ai.review.posting_policy import PostingPolicy
from lintro.ai.review.sticky import build_sticky_comment
from lintro.ai.review.sticky.heading import (
    FINDINGS_HEADING_PREFIX,
    parse_findings_heading,
)
from lintro.enums.confidence_level import ConfidenceLevel

_HEAD = "b0153e29169bfcb2b702b97ad92be5e4bf84929b"
_OTHER_HEAD = "0" * 40
_PATHS = ("a.py", "b.py")


def _diff(path: str, *, change: str = "change") -> str:
    """Return a one-hunk unified diff for *path*.

    Args:
        path: Repository path.
        change: The added line, so two diffs can differ.

    Returns:
        The diff text.
    """
    return (
        f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n"
        f"@@ -1,1 +1,2 @@\n context\n+{change}-{path}\n"
    )


def _context(
    *,
    head: str = _HEAD,
    diffs: dict[str, str] | None = None,
) -> ReviewContext:
    """Return a review context over *diffs* at *head*.

    Args:
        head: Head sha of the run.
        diffs: Per-path diffs; the two default paths when ``None``.

    Returns:
        The context.
    """
    diffs = diffs if diffs is not None else {path: _diff(path) for path in _PATHS}
    return ReviewContext(
        base_ref="main",
        head_ref=head,
        changed_files=[
            ChangedFile(path=path, status="modified", additions=1, deletions=0)
            for path in diffs
        ],
        unified_diff="\n".join(diffs.values()),
        pr_metadata=None,
    )


def _covered(
    *,
    context: ReviewContext,
    round_number: int = 1,
) -> tuple[CoverageRecord, ...]:
    """Return coverage records matching every file of *context* at its head.

    Args:
        context: The context whose diffs are covered.
        round_number: Round the records were written in.

    Returns:
        One record per changed file.
    """
    from lintro.ai.review.context.diff_parse import split_unified_diff_by_file

    diffs = split_unified_diff_by_file(unified_diff=context.unified_diff)
    return tuple(
        CoverageRecord(
            path=path,
            patch_hash=normalized_patch_hash(diff),
            reviewed_sha=context.head_ref,
            round=round_number,
        )
        for path, diff in diffs.items()
    )


def _run(*, sha: str = _HEAD, round_number: int = 1, **coverage: object) -> RunRecord:
    """Return a finished run record at *sha*.

    Args:
        sha: Head the run reviewed.
        round_number: Round number.
        **coverage: Overrides for the run's coverage block.

    Returns:
        The run record.
    """
    return RunRecord(
        identity=RunIdentity(round=round_number, sha=sha),
        coverage=RunCoverage(**coverage),  # type: ignore[arg-type]
    )


def _state(
    *,
    context: ReviewContext,
    runs: tuple[RunRecord, ...] | None = None,
    **extra: object,
) -> ReviewState:
    """Return a state whose coverage matches *context* and whose runs end at its head.

    Args:
        context: The context the state covers.
        runs: Run records; one finished run at the head when ``None``.
        **extra: Other state fields.

    Returns:
        The state.
    """
    return ReviewState(
        runs=runs if runs is not None else (_run(sha=context.head_ref),),
        coverage=_covered(context=context),
        **extra,  # type: ignore[arg-type]
    )


def _open(
    path: str,
    *,
    severity: Severity = Severity.P2,
    comment: int | None = 7,
) -> FindingRecord:
    """Return an open finding record on *path*.

    Args:
        path: File the finding sits on.
        severity: Severity.
        comment: Inline comment id, or ``None`` when never posted.

    Returns:
        The record.
    """
    return FindingRecord(
        fingerprint=f"fp-{path}",
        severity=severity,
        file=path,
        line=1,
        title=f"finding on {path}",
        status=FindingStatus.OPEN,
        inline_comment_id=comment,
        confidence="high",
    )


# --- the one shape that skips ----------------------------------------------------


def test_a_covered_head_is_already_reviewed() -> None:
    """Same head, finished run, every file covered: no round."""
    context = _context()
    prior = _state(context=context)

    decision = evaluate_already_reviewed(
        context=context,
        prior=prior,
        force_full=False,
        reusable_rounds=None,
        env={},
    )

    assert_that(decision).is_instance_of(AlreadyReviewed)
    assert decision is not None
    assert_that(decision.head_sha).is_equal_to(_HEAD)
    assert_that(decision.round).is_equal_to(1)
    assert_that(decision.files_covered).is_equal_to(2)
    assert_that(decision.open_findings).is_equal_to(0)


def test_open_threads_do_not_reopen_a_covered_head() -> None:
    """An open finding re-reads its file on a new head, not on the same one."""
    context = _context()
    prior = _state(context=context, findings=(_open("a.py"),))

    decision = evaluate_already_reviewed(
        context=context,
        prior=prior,
        force_full=False,
        reusable_rounds=None,
        env={},
    )

    assert decision is not None
    assert_that(decision.open_findings).is_equal_to(1)


def test_the_decision_describes_itself_for_the_summary() -> None:
    """One line names the head, the round, the coverage and what is carried."""
    decision = AlreadyReviewed(
        head_sha=_HEAD,
        round=3,
        files_covered=27,
        open_findings=1,
    )

    assert_that(decision.describe()).is_equal_to(
        "already reviewed at b0153e2 in round 3: 27 files covered, 1 open finding carried",
    )
    assert_that(decision.to_dict()).is_equal_to(
        {"head_sha": _HEAD, "round": 3, "files_covered": 27, "open_findings": 1},
    )


# --- every shape that must still review ------------------------------------------


def test_a_changed_head_reviews() -> None:
    """The last finished run reviewed another commit."""
    context = _context(head=_OTHER_HEAD)
    prior = _state(context=_context())

    assert_that(
        evaluate_already_reviewed(
            context=context,
            prior=prior,
            force_full=False,
            reusable_rounds=None,
            env={},
        ),
    ).is_none()


def test_a_changed_patch_reviews_even_on_the_same_head() -> None:
    """A file whose patch hash moved is directly changed."""
    context = _context(
        diffs={"a.py": _diff("a.py", change="new"), "b.py": _diff("b.py")},
    )
    prior = _state(context=_context())

    assert_that(
        evaluate_already_reviewed(
            context=context,
            prior=prior,
            force_full=False,
            reusable_rounds=None,
            env={},
        ),
    ).is_none()


def test_a_never_reviewed_file_reviews() -> None:
    """A file with no coverage record at all is never reviewed."""
    context = _context(diffs={path: _diff(path) for path in ("a.py", "b.py", "c.py")})
    prior = _state(context=_context())

    assert_that(
        evaluate_already_reviewed(
            context=context,
            prior=prior,
            force_full=False,
            reusable_rounds=None,
            env={},
        ),
    ).is_none()


def test_a_partial_last_run_reviews() -> None:
    """A round that stopped early did not cover the head."""
    context = _context()
    prior = _state(context=context, runs=(_run(partial=True),))

    assert_that(
        evaluate_already_reviewed(
            context=context,
            prior=prior,
            force_full=False,
            reusable_rounds=None,
            env={},
        ),
    ).is_none()


def test_a_degraded_last_run_reviews() -> None:
    """A degradation the next round owes a redo for is not a covered head."""
    context = _context()
    degraded = DegradationRecord.from_degradation(
        degradation=CoverageDegradation(
            reason=CoverageDegradationReason.ADVERSARIAL_SWEEP_FAILED,
            chunk_index=0,
            paths=("a.py",),
        ),
        head_sha=_HEAD,
    )
    prior = _state(context=context, runs=(_run(degradations=(degraded,)),))

    assert_that(
        evaluate_already_reviewed(
            context=context,
            prior=prior,
            force_full=False,
            reusable_rounds=None,
            env={},
        ),
    ).is_none()


def test_a_trailing_checkpoint_is_not_a_finished_run() -> None:
    """A checkpoint at this head after a finished run elsewhere does not count."""
    context = _context()
    prior = _state(
        context=context,
        runs=(_run(sha=_OTHER_HEAD), _run(sha=_HEAD, round_number=2, checkpoint=True)),
    )

    assert_that(
        evaluate_already_reviewed(
            context=context,
            prior=prior,
            force_full=False,
            reusable_rounds=None,
            env={},
        ),
    ).is_none()


def test_a_prior_without_run_records_reviews() -> None:
    """Coverage with no run record cannot say which head it reviewed."""
    context = _context()
    prior = ReviewState(coverage=_covered(context=context))

    assert_that(
        evaluate_already_reviewed(
            context=context,
            prior=prior,
            force_full=False,
            reusable_rounds=None,
            env={},
        ),
    ).is_none()


def test_no_prior_state_reviews() -> None:
    """A first round has nothing to skip on."""
    assert_that(
        evaluate_already_reviewed(
            context=_context(),
            prior=None,
            force_full=False,
            reusable_rounds=None,
            env={},
        ),
    ).is_none()


def test_a_diff_with_no_eligible_files_reviews() -> None:
    """Vacuous coverage authorises nothing."""
    context = _context(diffs={})
    prior = ReviewState(runs=(_run(),))

    assert_that(
        evaluate_already_reviewed(
            context=context,
            prior=prior,
            force_full=False,
            reusable_rounds=None,
            env={},
        ),
    ).is_none()


def test_a_model_flag_reviews() -> None:
    """A flag the next round owes is work, not a covered head."""
    context = _context()
    prior = _state(
        context=context,
        flagged_files=(FlaggedFile(path="b.py", reason="check the caller"),),
    )

    assert_that(
        evaluate_already_reviewed(
            context=context,
            prior=prior,
            force_full=False,
            reusable_rounds=None,
            env={},
        ),
    ).is_none()


def test_coverage_the_reuse_gate_rejects_reviews() -> None:
    """Records from a round the gate does not inherit are not coverage."""
    context = _context()
    prior = _state(context=context)

    assert_that(
        evaluate_already_reviewed(
            context=context,
            prior=prior,
            force_full=False,
            reusable_rounds=frozenset(),
            env={},
        ),
    ).is_none()


def test_full_and_the_force_variable_bypass_the_guard() -> None:
    """Both overrides run a round on a covered head."""
    context = _context()
    prior = _state(context=context)

    assert_that(
        evaluate_already_reviewed(
            context=context,
            prior=prior,
            force_full=True,
            reusable_rounds=None,
            env={},
        ),
    ).is_none()
    assert_that(
        evaluate_already_reviewed(
            context=context,
            prior=prior,
            force_full=False,
            reusable_rounds=None,
            env={FORCE_ENV: "1"},
        ),
    ).is_none()


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("1", True),
        ("true", True),
        (" Yes ", True),
        ("on", True),
        ("0", False),
        ("", False),
        ("no", False),
        ("false", False),
    ],
)
def test_the_force_variable_reads_only_truthy_spellings(
    value: str,
    expected: bool,
) -> None:
    """``0``, ``false`` and an empty value do not force a round."""
    assert_that(force_requested(env={FORCE_ENV: value})).is_equal_to(expected)


# --- publication evidence under --post ---------------------------------------------


_DECISION = AlreadyReviewed(head_sha=_HEAD, round=2, files_covered=2, open_findings=1)
_STICKY = f"intro\n### Findings · Round 2 · `{_HEAD[:7]}` · complete · 1 open · 0 fixed this round\n"
_DIFF_LINES = {"a.py": {1, 2}, "b.py": {5}}


def _policy(*, questions_inline: bool = False) -> PostingPolicy:
    """Return the default posting policy.

    Args:
        questions_inline: Whether questions post inline.

    Returns:
        The policy.
    """
    return PostingPolicy(
        inline_min_confidence=ConfidenceLevel.MEDIUM,
        post_questions_inline=questions_inline,
    )


def _published(
    *findings: FindingRecord,
    sticky: str | None = _STICKY,
    diff_lines: dict[str, set[int]] | None = _DIFF_LINES,
    policy: PostingPolicy | None = None,
    decision: AlreadyReviewed = _DECISION,
) -> bool:
    """Run the publication check over *findings*.

    Args:
        *findings: Tracked records.
        sticky: The sticky body the listing returns, or ``None`` for none.
        diff_lines: The PR diff lines the fetch returns.
        policy: Posting policy; the default when ``None``.
        decision: The guard's decision.

    Returns:
        The check's answer.
    """
    return publication_complete(
        prior=ReviewState(findings=findings),
        policy=policy or _policy(),
        decision=decision,
        find_sticky=lambda *, marker: (
            (1, sticky) if sticky and marker == STICKY_MARKER else None
        ),
        fetch_diff_lines=lambda: diff_lines,
    )


def test_publication_is_complete_with_the_rounds_sticky_and_threads() -> None:
    """The sticky names the round at this head and every diff-line P1/P2 has a thread."""
    assert_that(
        _published(_open("a.py"), _open("b.py", severity=Severity.P1)),
    ).is_true()


def test_the_heading_parser_reads_what_the_renderer_writes(
    sample_review_result: ReviewResult,
) -> None:
    """The guard's parser and the sticky heading cannot drift apart.

    A real board is rendered for round 2 at the head and parsed back, so a
    change to either side fails here.

    Args:
        sample_review_result: A complete review result fixture.
    """
    body = build_sticky_comment(
        request=StickyRequest(
            result=sample_review_result,
            prior_state=ReviewState(runs=(_run(sha=_OTHER_HEAD),)),
            head_sha=_HEAD,
        ),
    )

    assert_that(parse_findings_heading(body=body)).is_equal_to((2, _HEAD[:7]))


def test_the_heading_parser_handles_each_shape() -> None:
    """A sha, no sha, a sha in an unexpected form, and no heading at all."""
    assert_that(parse_findings_heading(body=_STICKY)).is_equal_to((2, _HEAD[:7]))
    assert_that(
        parse_findings_heading(body=f"{FINDINGS_HEADING_PREFIX}3 · complete"),
    ).is_equal_to((3, ""))
    assert_that(
        parse_findings_heading(body=f"{FINDINGS_HEADING_PREFIX}3 · `ABCDEF1` · x"),
    ).is_equal_to((3, "ABCDEF1"))
    assert_that(parse_findings_heading(body="no heading here")).is_none()


def test_a_sha_the_renderer_would_not_write_is_still_compared() -> None:
    """An unexpected sha form is read and rejected, not read as absent."""
    odd = _STICKY.replace(f"`{_HEAD[:7]}`", "`ZZZ`")

    assert_that(_published(sticky=odd)).is_false()


def test_a_missing_sticky_is_not_published() -> None:
    """Without the sticky the verdict never reached the PR."""
    assert_that(_published(_open("a.py"), sticky=None)).is_false()


def test_a_sticky_from_an_earlier_round_is_not_published() -> None:
    """A board rendered for round 1 cannot vouch for round 2's findings."""
    earlier = _STICKY.replace("Round 2", "Round 1")

    assert_that(_published(sticky=earlier)).is_false()


def test_a_sticky_for_another_head_is_not_published() -> None:
    """The heading's sha must be this head's."""
    other = _STICKY.replace(_HEAD[:7], _OTHER_HEAD[:7])

    assert_that(_published(sticky=other)).is_false()


def test_a_heading_without_a_sha_is_accepted_on_the_round_alone() -> None:
    """Older boards carried no sha; the round still has to match."""
    assert_that(_published(sticky=_STICKY.replace(f" · `{_HEAD[:7]}`", ""))).is_true()


def test_a_diff_line_finding_without_a_thread_is_not_published() -> None:
    """A P2 on a diff line the policy would post inline, with no comment id, means a failed batch."""
    assert_that(_published(_open("a.py", comment=None))).is_false()


def test_a_finding_off_the_diff_lines_is_sticky_only_by_design() -> None:
    """The poster anchors nothing there, so no thread id is expected."""
    off_diff = replace(_open("a.py", comment=None), line=40)

    assert_that(_published(off_diff)).is_true()


def test_unfetchable_diff_lines_read_as_not_published() -> None:
    """When the mapping cannot be established and a thread is expected, review."""
    assert_that(_published(_open("a.py", comment=None), diff_lines=None)).is_false()
    assert_that(
        _published(_open("a.py", severity=Severity.P3, comment=None), diff_lines=None),
    ).is_true()


@pytest.mark.parametrize(
    "record",
    [
        _open("a.py", severity=Severity.P3, comment=None),
        replace(_open("a.py", comment=None), confidence="low"),
        replace(_open("a.py", comment=None), kind=FindingKind.QUESTION),
        replace(_open("a.py", comment=None), status=FindingStatus.RESOLVED),
    ],
)
def test_records_the_policy_never_posts_inline_need_no_thread(
    record: FindingRecord,
) -> None:
    """A P3, a low-confidence finding, a question or a resolved record has no thread by design."""
    assert_that(_published(record)).is_true()


def test_a_question_posted_inline_by_configuration_needs_its_thread() -> None:
    """With questions opted into inline posting, an open question without an id is unpublished."""
    question = replace(_open("a.py", comment=None), kind=FindingKind.QUESTION)

    assert_that(_published(question, policy=_policy(questions_inline=True))).is_false()
