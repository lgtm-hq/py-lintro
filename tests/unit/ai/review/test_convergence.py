"""Scoring and stop-rule tests for the convergence signal (#2099)."""

from __future__ import annotations

import pytest
from assertpy import assert_that

from lintro.ai.review.convergence import (
    evaluate_convergence,
    format_convergence_stamp,
    format_score,
    format_trajectory,
    score_finding,
    score_records,
    score_trajectory,
)
from lintro.ai.review.enums.evidence_style import EvidenceStyle
from lintro.ai.review.enums.finding_kind import FindingKind
from lintro.ai.review.enums.finding_status import FindingStatus
from lintro.ai.review.models.finding_record import FindingRecord
from lintro.ai.review.models.review_finding import Severity
from lintro.ai.review.models.run_record import RunRecord


def _record(
    *,
    severity: Severity = Severity.P2,
    category: str = "logic-bug",
    confidence: str = "high",
    evidence_style: EvidenceStyle = EvidenceStyle.DIFF_LOCAL,
    status: FindingStatus = FindingStatus.OPEN,
    kind: FindingKind = FindingKind.FINDING,
    fingerprint: str = "a" * 16,
) -> FindingRecord:
    """Build a tracked finding record for scoring tests.

    Args:
        severity: Finding severity.
        category: Finding category label.
        confidence: Reported model confidence.
        evidence_style: Self-reported evidence basis.
        status: Whether the finding is open or resolved.
        kind: Whether the entry is a finding or a question.
        fingerprint: Identity hash for the record.

    Returns:
        The finding record.
    """
    return FindingRecord(
        fingerprint=fingerprint,
        severity=severity,
        category=category,
        confidence=confidence,
        evidence_style=evidence_style,
        status=status,
        kind=kind,
    )


def _run(
    *,
    round_number: int,
    score: float | None,
    partial: bool = False,
    coverage_limited: bool = False,
) -> RunRecord:
    """Build a run record carrying a recorded convergence score.

    Args:
        round_number: 1-based round number.
        score: Recorded convergence score, or ``None`` for a legacy record.
        partial: Whether the round stopped before reviewing every chunk.
        coverage_limited: Whether a findings cap may have suppressed findings.

    Returns:
        The run record.
    """
    return RunRecord(
        round=round_number,
        convergence_score=score,
        partial=partial,
        coverage_limited=coverage_limited,
    )


# --- per-finding scoring -----------------------------------------------------


@pytest.mark.parametrize(
    ("severity", "confidence", "evidence_style", "expected"),
    [
        (Severity.P1, "high", EvidenceStyle.DIFF_LOCAL, 10.0),
        (Severity.P1, "medium", EvidenceStyle.DIFF_LOCAL, 8.4),
        (Severity.P1, "low", EvidenceStyle.DIFF_LOCAL, 7.2),
        (Severity.P1, "high", EvidenceStyle.CROSS_FILE, 9.2),
        (Severity.P1, "high", EvidenceStyle.SPECULATIVE, 7.6),
        (Severity.P2, "high", EvidenceStyle.DIFF_LOCAL, 6.0),
        (Severity.P2, "medium", EvidenceStyle.CROSS_FILE, 4.44),
        (Severity.P2, "low", EvidenceStyle.SPECULATIVE, 3.36),
        (Severity.P3, "high", EvidenceStyle.DIFF_LOCAL, 3.0),
        (Severity.P3, "medium", EvidenceStyle.DIFF_LOCAL, 2.0),
        (Severity.P3, "low", EvidenceStyle.DIFF_LOCAL, 1.25),
        (Severity.P3, "low", EvidenceStyle.SPECULATIVE, 0.8),
    ],
    ids=[
        "p1=high+diff_local",
        "p1=medium+diff_local",
        "p1=low+diff_local",
        "p1=high+cross_file",
        "p1=high+speculative",
        "p2=high+diff_local",
        "p2=medium+cross_file",
        "p2=low+speculative",
        "p3=high+diff_local",
        "p3=medium+diff_local",
        "p3=low+diff_local",
        "p3=low+speculative",
    ],
)
def test_score_follows_the_floor_ceiling_band(
    severity: Severity,
    confidence: str,
    evidence_style: EvidenceStyle,
    expected: float,
) -> None:
    """Each severity/confidence/evidence combination scores on its band.

    The expected values are the documented contract, not a restatement of the
    tables: they are what a reader of the module docstring should be able to
    compute by hand.

    Args:
        severity: Finding severity under test.
        confidence: Reported confidence under test.
        evidence_style: Reported evidence basis under test.
        expected: Score the formula must produce.
    """
    score = score_finding(
        severity=severity,
        category="logic-bug",
        confidence=confidence,
        evidence_style=evidence_style,
    )

    assert_that(score).is_equal_to(expected)


def test_score_never_falls_below_the_severity_floor() -> None:
    """The weakest possible P1 still outranks the strongest possible P3."""
    weakest_p1 = score_finding(
        severity=Severity.P1,
        category="logic-bug",
        confidence="low",
        evidence_style=EvidenceStyle.SPECULATIVE,
    )
    strongest_p3 = score_finding(
        severity=Severity.P3,
        category="logic-bug",
        confidence="high",
        evidence_style=EvidenceStyle.DIFF_LOCAL,
    )

    assert_that(weakest_p1).is_greater_than(strongest_p3)


@pytest.mark.parametrize(
    "confidence",
    ["", "unknown", "HIGH-ish", "  "],
    ids=["confidence=empty", "confidence=unknown", "confidence=garbled", "spaces"],
)
def test_unreadable_confidence_scores_as_medium(confidence: str) -> None:
    """An unlabeled finding is not treated as the weakest possible one.

    Args:
        confidence: Unusable confidence label under test.
    """
    scored = score_finding(
        severity=Severity.P2,
        category="logic-bug",
        confidence=confidence,
    )
    as_medium = score_finding(
        severity=Severity.P2,
        category="logic-bug",
        confidence="medium",
    )

    assert_that(scored).is_equal_to(as_medium)


def test_confidence_label_is_case_insensitive() -> None:
    """A model that shouts its confidence is read the same way."""
    shouted = score_finding(
        severity=Severity.P1,
        category="logic-bug",
        confidence="HIGH",
    )

    assert_that(shouted).is_equal_to(10.0)


@pytest.mark.parametrize(
    "category",
    ["contract-drift", "breaking-change"],
    ids=["category=contract_drift", "category=breaking_change"],
)
def test_systemic_categories_ignore_the_evidence_discount(category: str) -> None:
    """A systemic finding scores at full likelihood however it was reached.

    Args:
        category: Systemic category label under test.
    """
    speculative = score_finding(
        severity=Severity.P2,
        category=category,
        confidence="high",
        evidence_style=EvidenceStyle.SPECULATIVE,
    )
    diff_local = score_finding(
        severity=Severity.P2,
        category=category,
        confidence="high",
        evidence_style=EvidenceStyle.DIFF_LOCAL,
    )

    assert_that(speculative).is_equal_to(diff_local)


def test_non_systemic_category_still_takes_the_evidence_discount() -> None:
    """The systemic exemption is scoped, not a blanket ceiling."""
    speculative = score_finding(
        severity=Severity.P2,
        category="code-smell",
        confidence="high",
        evidence_style=EvidenceStyle.SPECULATIVE,
    )

    assert_that(speculative).is_less_than(6.0)


# --- aggregation -------------------------------------------------------------


def test_round_score_sums_the_open_findings() -> None:
    """The round score is the sum over what is still open."""
    records = (
        _record(severity=Severity.P1, fingerprint="a" * 16),
        _record(severity=Severity.P3, fingerprint="b" * 16),
    )

    assert_that(score_records(records=records)).is_equal_to(13.0)


def test_resolved_findings_contribute_nothing() -> None:
    """A round that fixed everything scores zero, not its history."""
    records = (
        _record(severity=Severity.P1, status=FindingStatus.RESOLVED),
        _record(
            severity=Severity.P1,
            status=FindingStatus.RESOLVED,
            fingerprint="b" * 16,
        ),
    )

    assert_that(score_records(records=records)).is_equal_to(0.0)


def test_questions_are_excluded_like_they_are_from_the_verdict() -> None:
    """An open question is not unresolved risk."""
    with_question = (
        _record(severity=Severity.P2),
        _record(severity=Severity.P1, kind=FindingKind.QUESTION, fingerprint="b" * 16),
    )
    without_question = (_record(severity=Severity.P2),)

    assert_that(score_records(records=with_question)).is_equal_to(
        score_records(records=without_question),
    )


def test_scoring_is_order_independent() -> None:
    """The same finding set always scores the same number."""
    first = _record(severity=Severity.P1, fingerprint="a" * 16)
    second = _record(severity=Severity.P3, fingerprint="b" * 16)

    assert_that(score_records(records=(first, second))).is_equal_to(
        score_records(records=(second, first)),
    )


def test_empty_round_scores_zero() -> None:
    """A review that tracked nothing has nothing outstanding."""
    assert_that(score_records(records=())).is_equal_to(0.0)


# --- trajectory --------------------------------------------------------------


def test_trajectory_skips_rounds_recorded_before_scoring_existed() -> None:
    """A legacy round is absent from the trajectory, not a fabricated zero."""
    runs = (
        _run(round_number=1, score=None),
        _run(round_number=2, score=4.5),
        _run(round_number=3, score=1.25),
    )

    assert_that(score_trajectory(runs=runs)).is_equal_to((4.5, 1.25))


def test_trajectory_renders_oldest_first() -> None:
    """The rendered chain reads in the direction the review moved."""
    assert_that(format_trajectory(scores=(4.5, 1.25))).is_equal_to("4.50 → 1.25")


def test_scores_render_at_a_fixed_precision() -> None:
    """Every surface shows the same digits for the same score."""
    assert_that(format_score(score=1.5)).is_equal_to("1.50")


# --- stop rule ---------------------------------------------------------------


def test_no_threshold_never_converges() -> None:
    """Disabled by default: an unset threshold always reviews again."""
    runs = (_run(round_number=1, score=0.0), _run(round_number=2, score=0.0))

    decision = evaluate_convergence(runs=runs, threshold=None, stable_rounds=2)

    assert_that(decision.converged).is_false()
    assert_that(decision.trajectory).is_equal_to((0.0, 0.0))


def test_converges_after_the_required_consecutive_quiet_rounds() -> None:
    """Two sub-threshold rounds in a row stop the treadmill."""
    runs = (
        _run(round_number=1, score=9.0),
        _run(round_number=2, score=1.0),
        _run(round_number=3, score=0.5),
    )

    decision = evaluate_convergence(runs=runs, threshold=3.0, stable_rounds=2)

    assert_that(decision.converged).is_true()
    assert_that(decision.round_number).is_equal_to(4)
    assert_that(decision.score).is_equal_to(0.5)
    assert_that(decision.threshold).is_equal_to(3.0)


def test_one_quiet_round_is_not_enough() -> None:
    """A single quiet round can be noise; the streak is the signal."""
    runs = (_run(round_number=1, score=9.0), _run(round_number=2, score=0.5))

    decision = evaluate_convergence(runs=runs, threshold=3.0, stable_rounds=2)

    assert_that(decision.converged).is_false()


def test_a_streak_shorter_than_the_history_is_not_enough() -> None:
    """Fewer recorded rounds than required can never converge."""
    runs = (_run(round_number=1, score=0.5),)

    decision = evaluate_convergence(runs=runs, threshold=3.0, stable_rounds=2)

    assert_that(decision.converged).is_false()


def test_a_score_at_the_threshold_does_not_converge() -> None:
    """The comparison is strict, so the threshold itself still reviews."""
    runs = (_run(round_number=1, score=3.0), _run(round_number=2, score=3.0))

    decision = evaluate_convergence(runs=runs, threshold=3.0, stable_rounds=2)

    assert_that(decision.converged).is_false()


def test_a_loud_round_inside_the_window_resets_the_streak() -> None:
    """Only the most recent rounds count — an old quiet pair does not."""
    runs = (
        _run(round_number=1, score=0.5),
        _run(round_number=2, score=0.5),
        _run(round_number=3, score=9.0),
    )

    decision = evaluate_convergence(runs=runs, threshold=3.0, stable_rounds=2)

    assert_that(decision.converged).is_false()


@pytest.mark.parametrize(
    ("partial", "coverage_limited"),
    [(True, False), (False, True)],
    ids=["degraded=partial", "degraded=coverage_limited"],
)
def test_a_degraded_round_cannot_attest_stability(
    partial: bool,
    coverage_limited: bool,
) -> None:
    """A round that never looked properly is not evidence of quiet.

    Args:
        partial: Whether the round stopped with chunks unreviewed.
        coverage_limited: Whether a findings cap may have hidden findings.
    """
    runs = (
        _run(round_number=1, score=0.5),
        _run(
            round_number=2,
            score=0.5,
            partial=partial,
            coverage_limited=coverage_limited,
        ),
    )

    decision = evaluate_convergence(runs=runs, threshold=3.0, stable_rounds=2)

    assert_that(decision.converged).is_false()


def test_an_unscored_round_in_the_window_cannot_converge() -> None:
    """History from before scoring existed is not evidence either way."""
    runs = (_run(round_number=1, score=None), _run(round_number=2, score=0.5))

    decision = evaluate_convergence(runs=runs, threshold=3.0, stable_rounds=2)

    assert_that(decision.converged).is_false()


def test_no_history_never_converges() -> None:
    """A first round always runs."""
    decision = evaluate_convergence(runs=(), threshold=3.0, stable_rounds=2)

    assert_that(decision.converged).is_false()
    assert_that(decision.trajectory).is_empty()


def test_stable_rounds_of_one_converges_on_a_single_quiet_round() -> None:
    """The streak length is honored, not hardcoded to the default."""
    runs = (_run(round_number=1, score=9.0), _run(round_number=2, score=0.5))

    decision = evaluate_convergence(runs=runs, threshold=3.0, stable_rounds=1)

    assert_that(decision.converged).is_true()
    assert_that(decision.stable_rounds).is_equal_to(1)


@pytest.mark.parametrize(
    "threshold",
    [float("nan"), float("inf"), -1.0],
    ids=["threshold=nan", "threshold=inf", "threshold=negative"],
)
def test_an_unusable_threshold_disables_the_rule(threshold: float) -> None:
    """The rule fails toward reviewing, never toward skipping.

    Args:
        threshold: Unusable threshold value under test.
    """
    runs = (_run(round_number=1, score=0.0), _run(round_number=2, score=0.0))

    decision = evaluate_convergence(
        runs=runs,
        threshold=threshold,
        stable_rounds=2,
    )

    assert_that(decision.converged).is_false()


def test_the_stamp_names_the_round_score_and_threshold() -> None:
    """The stop is always explained in the same words on every surface."""
    runs = (_run(round_number=1, score=1.0), _run(round_number=2, score=0.5))
    decision = evaluate_convergence(runs=runs, threshold=3.0, stable_rounds=2)

    stamp = format_convergence_stamp(decision=decision)

    assert_that(stamp).is_equal_to(
        "converged at round 3 (score 0.50 < threshold 3.00)",
    )


def test_a_decision_serializes_its_whole_case() -> None:
    """The machine-readable outcome carries the evidence, not just a flag."""
    runs = (_run(round_number=1, score=1.0), _run(round_number=2, score=0.5))
    decision = evaluate_convergence(runs=runs, threshold=3.0, stable_rounds=2)

    payload = decision.to_dict()

    assert_that(payload["round"]).is_equal_to(3)
    assert_that(payload["score"]).is_equal_to(0.5)
    assert_that(payload["threshold"]).is_equal_to(3.0)
    assert_that(payload["stable_rounds"]).is_equal_to(2)
    assert_that(payload["trajectory"]).is_equal_to([1.0, 0.5])


def test_carried_finding_keeps_its_scored_evidence_style() -> None:
    """A carried finding is not re-scored on a changed evidence label."""
    from dataclasses import replace

    from lintro.ai.review.finding_matcher import _merge_pair

    prior = FindingRecord(
        fingerprint="fp",
        severity=Severity.P2,
        evidence_style=EvidenceStyle.DIFF_LOCAL,
        status=FindingStatus.OPEN,
    )
    current = replace(prior, evidence_style=EvidenceStyle.SPECULATIVE)

    carried, _outcome = _merge_pair(prior=prior, current=current)
    regressed, _outcome = _merge_pair(
        prior=replace(prior, status=FindingStatus.RESOLVED),
        current=current,
    )

    assert_that(carried.evidence_style).is_equal_to(EvidenceStyle.DIFF_LOCAL)
    assert_that(regressed.evidence_style).is_equal_to(EvidenceStyle.SPECULATIVE)
