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
from lintro.ai.review.finding_matcher import match_findings
from lintro.ai.review.models.finding_match_result import FindingMatchResult
from lintro.ai.review.models.finding_record import FindingRecord
from lintro.ai.review.models.review_finding import ReviewFinding, Severity
from lintro.ai.review.models.review_state import ReviewState
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


def _matched(
    *,
    round_number: int,
    style: EvidenceStyle | None = None,
    previous: ReviewState | None = None,
) -> FindingMatchResult:
    """Run one matching round through the public ``match_findings`` seam.

    Args:
        round_number: Round being matched.
        style: Evidence basis this round reports, or ``None`` to report no
            findings at all (which resolves the prior one).
        previous: State persisted by the prior round.

    Returns:
        The match result for the round.
    """
    findings = (
        ()
        if style is None
        else (
            ReviewFinding(
                title="Leak",
                file="a.py",
                line=10,
                severity=Severity.P2,
                category="logic-bug",
                confidence="high",
                evidence_style=style,
                description="d",
                cause="c",
                fix="f",
            ),
        )
    )
    return match_findings(
        previous=previous,
        findings=findings,
        round_number=round_number,
        head_sha=f"sha{round_number}",
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
    """A carried finding is not re-scored on a changed evidence label.

    Driven through the public ``match_findings`` seam production uses, so a
    later change that dropped or overwrote ``evidence_style`` after the merge
    fails here rather than passing on the private helper.
    """
    first = _matched(round_number=1, style=EvidenceStyle.DIFF_LOCAL)
    carried = _matched(
        round_number=2,
        style=EvidenceStyle.SPECULATIVE,
        previous=ReviewState(findings=first.records),
    )

    assert_that(carried.carried).is_length(1)
    assert_that(carried.carried[0].evidence_style).is_equal_to(
        EvidenceStyle.DIFF_LOCAL,
    )


def test_regressed_finding_is_re_scored_on_its_new_evidence() -> None:
    """A regression is a fresh sighting, so it adopts this round's basis."""
    first = _matched(round_number=1, style=EvidenceStyle.DIFF_LOCAL)
    resolved = _matched(round_number=2, previous=ReviewState(findings=first.records))
    regressed = _matched(
        round_number=3,
        style=EvidenceStyle.SPECULATIVE,
        previous=ReviewState(findings=resolved.records),
    )

    assert_that(resolved.resolved).is_length(1)
    assert_that(regressed.regressed).is_length(1)
    assert_that(regressed.regressed[0].evidence_style).is_equal_to(
        EvidenceStyle.SPECULATIVE,
    )


@pytest.mark.parametrize("field", ["threshold", "stable_rounds"])
def test_config_rejects_boolean_values(field: str) -> None:
    """A YAML ``true`` must not coerce into a numeric setting that arms the rule.

    Args:
        field: Config field under test.
    """
    from pydantic import ValidationError

    from lintro.config.review_config import ReviewConvergenceConfig

    with pytest.raises(ValidationError):
        ReviewConvergenceConfig(**{field: True})


@pytest.mark.parametrize("threshold", [True, False, "3.0", 0, 0.0])
def test_unusable_or_zero_thresholds_disable_the_rule(threshold: object) -> None:
    """Booleans, strings, and zero never arm the stop rule.

    Args:
        threshold: Raw threshold value under test.
    """
    runs = (
        RunRecord(round=1, convergence_score=0.0),
        RunRecord(round=2, convergence_score=0.0),
    )

    decision = evaluate_convergence(
        runs=runs,
        threshold=threshold,  # type: ignore[arg-type]
        stable_rounds=2,
    )

    assert_that(decision.converged).is_false()
    assert_that(decision.threshold).is_none()


@pytest.mark.parametrize("stable_rounds", [0, -1, True, "2"])
def test_unusable_stable_rounds_never_converge(stable_rounds: object) -> None:
    """A streak length that is not a positive int disables the rule.

    ``True`` is an ``int`` subclass and must not read as a one-round streak.

    Args:
        stable_rounds: Raw streak length under test.
    """
    runs = (
        RunRecord(round=1, convergence_score=0.0),
        RunRecord(round=2, convergence_score=0.0),
    )

    decision = evaluate_convergence(
        runs=runs,
        threshold=3.0,
        stable_rounds=stable_rounds,  # type: ignore[arg-type]
    )

    assert_that(decision.converged).is_false()


def test_unmeasured_latest_round_leaves_the_score_unset() -> None:
    """A window whose latest round was never scored reports no score at all."""
    runs = (
        RunRecord(round=1, convergence_score=0.5),
        RunRecord(round=2, convergence_score=None),
    )

    decision = evaluate_convergence(runs=runs, threshold=3.0, stable_rounds=2)

    assert_that(decision.converged).is_false()
    assert_that(decision.score).is_none()
    assert_that(decision.to_dict()["score"]).is_none()


def test_default_decision_serializes_without_fabricated_numbers() -> None:
    """A decision that never ran carries nulls, not zeros, on the wire."""
    from lintro.ai.review.models.convergence_decision import ConvergenceDecision

    payload = ConvergenceDecision().to_dict()

    assert_that(payload["score"]).is_none()
    assert_that(payload["threshold"]).is_none()
    assert_that(payload["trajectory"]).is_empty()


def test_score_at_the_threshold_is_not_quiet() -> None:
    """Quiet means strictly below: a score equal to the threshold still reviews."""
    runs = (
        RunRecord(round=1, convergence_score=3.0),
        RunRecord(round=2, convergence_score=3.0),
    )

    decision = evaluate_convergence(runs=runs, threshold=3.0, stable_rounds=2)

    assert_that(decision.converged).is_false()


def test_carried_finding_scores_on_its_original_likelihood() -> None:
    """The matcher's carry rule keeps the score a finding was first given.

    The first sighting is deliberately *not* the default style, so a merge
    that dropped the field (falling back to ``DIFF_LOCAL``) would change the
    score. Driven through ``match_findings``, not the private merge helper.
    """
    first = _matched(round_number=1, style=EvidenceStyle.SPECULATIVE)
    carried = _matched(
        round_number=2,
        style=EvidenceStyle.DIFF_LOCAL,
        previous=ReviewState(findings=first.records),
    )

    assert_that(score_records(records=carried.records)).is_equal_to(
        score_records(records=first.records),
    )
    assert_that(score_records(records=carried.records)).is_not_equal_to(
        score_records(records=(_record(evidence_style=EvidenceStyle.DIFF_LOCAL),)),
    )


@pytest.mark.parametrize(
    "raw",
    ["  speculative  ", "SPECULATIVE", "Speculative\n"],
    ids=["padded", "uppercase", "trailing newline"],
)
def test_a_padded_evidence_label_decodes_to_its_real_member(raw: str) -> None:
    """Whitespace and case must not silently re-score a finding.

    The blob decoder and the model-response normalizer share one parser, so
    neither can drift on trimming: a padded ``speculative`` that fell back to
    ``diff_local`` would score at the highest likelihood instead of the
    lowest, inflating the round score by a factor of 2.5 on that finding.

    Args:
        raw: Raw persisted label under test.
    """
    record = FindingRecord.from_dict(
        {
            "fingerprint": "a" * 16,
            "severity": "P2",
            "category": "logic-bug",
            "confidence": "high",
            "status": "open",
            "evidence_style": raw,
        },
    )

    assert record is not None
    assert_that(record.evidence_style).is_equal_to(EvidenceStyle.SPECULATIVE)
    assert_that(score_records(records=(record,))).is_not_equal_to(
        score_records(records=(_record(evidence_style=EvidenceStyle.DIFF_LOCAL),)),
    )


def test_a_v2_record_without_an_evidence_style_scores_at_diff_local() -> None:
    """An upgraded blob scores rather than raising on the missing key.

    A v2 state blob carries no ``evidence_style``, and the likelihood table is
    indexed directly. Decoding must therefore land on a real member — the
    ``diff_local`` default, which is the *highest* likelihood, so a missing
    label can never deflate a PR toward an early stop.
    """
    v2_payload = {
        "fingerprint": "a" * 16,
        "severity": "P2",
        "category": "logic-bug",
        "confidence": "high",
        "status": "open",
    }
    record = FindingRecord.from_dict(v2_payload)

    assert_that(record).is_not_none()
    assert record is not None
    assert_that(record.evidence_style).is_equal_to(EvidenceStyle.DIFF_LOCAL)
    assert_that(score_records(records=(record,))).is_equal_to(
        score_records(records=(_record(evidence_style=EvidenceStyle.DIFF_LOCAL),)),
    )


@pytest.mark.parametrize(
    "bad_score",
    [float("nan"), float("inf"), float("-inf")],
    ids=["nan", "inf", "-inf"],
)
def test_a_non_finite_score_is_never_serialized(bad_score: float) -> None:
    """A corrupt score is dropped, never written as invalid JSON.

    ``json.dumps`` emits a bare ``NaN``/``Infinity`` token for a non-finite
    float, which no strict JSON reader accepts — one such value would make the
    whole state blob undecodable for every later round.

    Args:
        bad_score: Unusable score value under test.
    """
    import json

    payload = RunRecord(round=1, convergence_score=bad_score).to_dict()

    assert_that(payload).does_not_contain_key("convergence_score")
    assert_that(json.loads(json.dumps(payload, allow_nan=False))).is_equal_to(payload)


def test_a_negative_persisted_score_decodes_as_unmeasured() -> None:
    """A negative score is valid JSON but impossible, so it never scores.

    Scores are non-negative by construction, and zero is the strongest
    possible evidence of convergence — so a corrupt negative must degrade to
    "not measured" rather than to the quietest round imaginable.
    """
    decoded = RunRecord.from_dict({"round": 1, "convergence_score": -1.0})

    assert_that(decoded.convergence_score).is_none()


@pytest.mark.parametrize(
    "bad_score",
    [float("nan"), float("inf"), -1.0],
    ids=["nan", "inf", "negative"],
)
def test_an_unusable_in_memory_score_never_reaches_the_decision(
    bad_score: float,
) -> None:
    """The decision reports "not measured" rather than a nonsense number.

    Args:
        bad_score: Unusable score value under test.
    """
    runs = (
        RunRecord(round=1, convergence_score=0.5),
        RunRecord(round=2, convergence_score=bad_score),
    )

    decision = evaluate_convergence(runs=runs, threshold=3.0, stable_rounds=2)

    assert_that(decision.converged).is_false()
    assert_that(decision.score).is_none()
    assert_that(decision.to_dict()["score"]).is_none()


def test_pending_resume_work_refuses_to_converge() -> None:
    """A quiet score does not attest that every file has been looked at.

    ``carry_unserved_flags`` keeps a model-flagged path whose file was not
    covered, and ``pending_invalidations_for`` keeps unserved group/import
    re-reads. A round can finish complete *and* quiet while queueing either
    for the next round, so the partial/coverage_limited guard does not cover
    this — skipping would drop that work rather than deferring it (#2099).
    """
    runs = (
        RunRecord(round=1, convergence_score=0.5),
        RunRecord(round=2, convergence_score=0.5),
    )

    without = evaluate_convergence(runs=runs, threshold=3.0, stable_rounds=2)
    with_pending = evaluate_convergence(
        runs=runs,
        threshold=3.0,
        stable_rounds=2,
        pending_resume_work=True,
    )

    # The window is otherwise identical, so the ledger is the only difference.
    assert_that(without.converged).is_true()
    assert_that(with_pending.converged).is_false()
    # The signal is still rendered: the round is deferred, not hidden.
    assert_that(with_pending.trajectory).is_equal_to(without.trajectory)


def test_a_boolean_score_never_reads_as_a_quiet_round() -> None:
    """``True`` must not arm the rule as the very quiet score 1.0.

    ``bool`` is an ``int`` subclass. Decoding already drops booleans, so this
    guards the in-memory path, where a rule that skips reviews must never
    switch itself on by accident.
    """
    runs = (
        RunRecord(round=1, convergence_score=True),
        RunRecord(round=2, convergence_score=True),
    )

    decision = evaluate_convergence(runs=runs, threshold=3.0, stable_rounds=2)

    assert_that(decision.converged).is_false()


def test_a_boolean_score_is_dropped_on_decode() -> None:
    """The decode path refuses a boolean the same way the config does."""
    decoded = RunRecord.from_dict({"round": 1, "convergence_score": True})

    assert_that(decoded.convergence_score).is_none()


def test_stamp_refuses_a_decision_that_did_not_converge() -> None:
    """A round that will still run can never be stamped as converged.

    A non-converged decision still carries ``score``, ``threshold`` and
    ``round_number`` so surfaces can render the stability signal — so
    checking only those would let a mistaken caller write "converged at
    round N" onto a board for a round that is about to review.
    """
    runs = (
        RunRecord(round=1, convergence_score=5.0),
        RunRecord(round=2, convergence_score=5.0),
    )
    decision = evaluate_convergence(runs=runs, threshold=3.0, stable_rounds=2)

    # Everything the old guard checked is present; only `converged` is False.
    assert_that(decision.converged).is_false()
    assert_that(decision.score).is_not_none()
    assert_that(decision.threshold).is_not_none()
    with pytest.raises(ValueError, match="converged decision"):
        format_convergence_stamp(decision=decision)


@pytest.mark.parametrize(
    "bad_score",
    [float("nan"), float("inf"), float("-inf"), -1.0, True],
    ids=["nan", "inf", "-inf", "negative", "boolean"],
)
def test_the_trajectory_omits_unusable_scores(bad_score: float) -> None:
    """A corrupt score never reaches the rendered chart or the JSON payload.

    The trajectory is built from in-memory records as well as decoded ones,
    and it is both rendered on the sticky and serialized into the envelope —
    a NaN would print as a nonsense point and make the payload invalid JSON.

    Args:
        bad_score: Unusable score value under test.
    """
    runs = (
        RunRecord(round=1, convergence_score=1.0),
        RunRecord(round=2, convergence_score=bad_score),
    )

    decision = evaluate_convergence(runs=runs, threshold=3.0, stable_rounds=2)

    assert_that(score_trajectory(runs=runs)).is_equal_to((1.0,))
    assert_that(decision.trajectory).is_equal_to((1.0,))
    assert_that(decision.to_dict()["trajectory"]).is_equal_to([1.0])


def test_stamp_refuses_a_decision_without_a_measured_score() -> None:
    """The stamp never renders a fabricated 0.00 for an unset value."""
    from lintro.ai.review.models.convergence_decision import ConvergenceDecision

    with pytest.raises(ValueError, match="measured score"):
        format_convergence_stamp(decision=ConvergenceDecision(converged=True))


@pytest.mark.parametrize("bad", [float("-inf"), float("nan"), -1.0])
def test_non_finite_or_negative_stored_scores_are_not_quiet(bad: float) -> None:
    """A corrupt in-memory score can never attest a quiet round.

    Args:
        bad: Stored score that must not count as below the threshold.
    """
    runs = (
        RunRecord(round=1, convergence_score=0.5),
        RunRecord(round=2, convergence_score=bad),
    )

    decision = evaluate_convergence(runs=runs, threshold=3.0, stable_rounds=2)

    assert_that(decision.converged).is_false()
