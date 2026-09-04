"""GitHub-surface rendering of the convergence signal (#2099).

Everything here goes through the public builders — ``build_sticky_comment``,
``advance_review_state``, ``render_state_sticky`` — so the assertions describe
what a reviewer actually reads on the pull request rather than the shape of a
private section helper.
"""

from __future__ import annotations

from dataclasses import replace

from assertpy import assert_that

from lintro.ai.review.convergence import evaluate_convergence
from lintro.ai.review.github_constants import (
    MAX_COMMENT_CHARS,
    PRIMARY_SOFT_LIMIT,
    STATE_MARKER_PREFIX,
)
from lintro.ai.review.github_render import format_convergence_banner
from lintro.ai.review.github_sticky import (
    advance_review_state,
    build_sticky_comment,
    render_state_sticky,
)
from lintro.ai.review.models.review_finding import ReviewFinding, Severity
from lintro.ai.review.models.review_result import ReviewResult
from lintro.ai.review.models.review_state import ReviewState
from lintro.ai.review.models.run_record import RunRecord


def _finding(
    *,
    title: str,
    severity: Severity = Severity.P1,
    line: int = 10,
) -> ReviewFinding:
    """Build a review finding for convergence rendering tests.

    Args:
        title: Finding title.
        severity: Finding severity.
        line: Line number the finding is anchored to.

    Returns:
        The review finding.
    """
    return ReviewFinding(
        severity=severity,
        category="security",
        file="src/example.py",
        line=line,
        title=title,
        description="Stores an application password as a module-level literal.",
        cause="Assigned at module scope.",
        fix="Read it from the environment.",
        confidence="high",
    )


def _body_only(*, body: str) -> str:
    """Strip any hidden state blob so assertions see rendered Markdown only.

    Args:
        body: Rendered sticky body.

    Returns:
        The visible Markdown.
    """
    return body.split(STATE_MARKER_PREFIX, 1)[0]


def test_a_completed_round_records_its_score_in_state(
    sample_review_result: ReviewResult,
) -> None:
    """The persisted run carries the score the stop rule later compares.

    Args:
        sample_review_result: Baseline review result fixture.
    """
    result = replace(
        sample_review_result,
        findings=(_finding(title="Leak"), _finding(title="Nit", line=44)),
    )

    state = advance_review_state(result=result, head_sha="sha1")

    assert_that(state.runs[-1].convergence_score).is_equal_to(20.0)


def test_a_round_that_fixed_everything_records_a_zero_score(
    sample_review_result: ReviewResult,
) -> None:
    """An empty round is quiet, and says so numerically.

    Args:
        sample_review_result: Baseline review result fixture.
    """
    first = advance_review_state(
        result=replace(sample_review_result, findings=(_finding(title="Leak"),)),
        head_sha="sha1",
    )

    second = advance_review_state(
        result=replace(sample_review_result, findings=()),
        prior_state=first,
        head_sha="sha2",
    )

    assert_that(second.runs[-1].convergence_score).is_equal_to(0.0)


def test_the_sticky_shows_the_score_on_the_first_round(
    sample_review_result: ReviewResult,
) -> None:
    """A reviewer sees the stability signal from round one.

    Args:
        sample_review_result: Baseline review result fixture.
    """
    body = _body_only(
        body=build_sticky_comment(
            result=replace(sample_review_result, findings=(_finding(title="Leak"),)),
            head_sha="sha1",
        ),
    )

    assert_that(body).contains("Convergence score 10.00")
    assert_that(body).does_not_contain("trajectory")


def test_the_sticky_shows_the_trajectory_once_there_is_one(
    sample_review_result: ReviewResult,
) -> None:
    """The arrow chain is what says whether the review is settling.

    Args:
        sample_review_result: Baseline review result fixture.
    """
    first = advance_review_state(
        result=replace(
            sample_review_result,
            findings=(_finding(title="Leak"), _finding(title="Nit", line=44)),
        ),
        head_sha="sha1",
    )

    body = _body_only(
        body=build_sticky_comment(
            result=replace(
                sample_review_result,
                findings=(_finding(title="Leak"),),
            ),
            prior_state=first,
            head_sha="sha2",
        ),
    )

    assert_that(body).contains("Convergence score 10.00 · trajectory 20.00 → 10.00")


def test_a_clean_round_still_shows_its_score(
    sample_review_result: ReviewResult,
) -> None:
    """The signal survives the "nothing open" short section.

    Args:
        sample_review_result: Baseline review result fixture.
    """
    body = _body_only(
        body=build_sticky_comment(
            result=replace(sample_review_result, findings=()),
            head_sha="sha1",
        ),
    )

    assert_that(body).contains("✅ Nothing open.")
    assert_that(body).contains("Convergence score 0.00")


def test_legacy_state_renders_no_convergence_line() -> None:
    """History from before scoring existed says nothing rather than zero."""
    state = ReviewState(runs=(RunRecord(round=1, sha="sha1", model="claude"),))

    body = render_state_sticky(state=state)

    assert_that(body).does_not_contain("Convergence score")


def test_the_sticky_stays_under_its_size_caps(
    sample_review_result: ReviewResult,
) -> None:
    """The added line must not push the primary comment over its budget.

    Args:
        sample_review_result: Baseline review result fixture.
    """
    body = build_sticky_comment(
        result=replace(
            sample_review_result,
            findings=tuple(
                _finding(title=f"Finding {index}", line=index) for index in range(1, 60)
            ),
        ),
        head_sha="sha1",
    )

    assert_that(len(body)).is_less_than_or_equal_to(MAX_COMMENT_CHARS)
    assert_that(len(body)).is_less_than_or_equal_to(PRIMARY_SOFT_LIMIT)
    # Fitting under the cap must not be achieved by dropping the signal the
    # line exists to carry: a fitter that shed it would still pass a pure
    # length assertion (#2099 review).
    assert_that(body).contains("Convergence score")


def test_the_converged_banner_stamps_the_board_it_re_renders(
    sample_review_result: ReviewResult,
) -> None:
    """A skipped round explains itself *over the last good board*.

    The board carries real open findings, so a stamp that blanked it — or
    rendered the empty-state page under the banner — fails here. An empty
    board could not distinguish the two.

    Args:
        sample_review_result: Baseline review result fixture.
    """
    state = replace(
        advance_review_state(result=sample_review_result, head_sha="a" * 40),
        runs=(
            RunRecord(round=1, sha="sha1", model="claude", convergence_score=1.0),
            RunRecord(round=2, sha="sha2", model="claude", convergence_score=0.5),
        ),
    )
    decision = evaluate_convergence(
        runs=state.runs,
        threshold=3.0,
        stable_rounds=2,
    )

    body = render_state_sticky(
        state=state,
        banner=format_convergence_banner(decision=decision),
    )

    assert_that(body).contains("converged at round 3 (score 0.50 < threshold 3.00)")
    assert_that(body).contains("No provider call")
    assert_that(body).contains("--full")
    assert_that(state.findings).is_not_empty()
    for record in state.findings:
        assert_that(body).contains(record.title)


def test_the_converged_banner_names_the_streak_that_earned_the_stop() -> None:
    """A reader can tell how much evidence the stop rule had."""
    runs = (
        RunRecord(round=1, convergence_score=1.0),
        RunRecord(round=2, convergence_score=0.5),
        RunRecord(round=3, convergence_score=0.5),
    )
    decision = evaluate_convergence(runs=runs, threshold=3.0, stable_rounds=3)

    banner = format_convergence_banner(decision=decision)

    assert_that(banner).contains("over 3 consecutive rounds")
