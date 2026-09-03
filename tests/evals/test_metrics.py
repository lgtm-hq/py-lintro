"""Tests for the stability, agreement and efficacy metrics."""

from __future__ import annotations

from assertpy import assert_that
from review_matrix.enums.run_status import RunStatus
from review_matrix.findings import fingerprints_for
from review_matrix.metrics import (
    config_stability,
    cross_config_agreement,
    efficacy_against_labels,
    finding_match_rate,
    jaccard_index,
    mean_or_none,
)
from review_matrix.models.corpus import Corpus, CorpusItem, LabeledFinding
from review_matrix.models.run import EvalRun

from lintro.ai.review.enums.review_verdict import ReviewVerdict
from lintro.ai.review.models.review_finding import ReviewFinding, Severity
from tests.evals.helpers import make_finding


def _run(
    *,
    config_id: str,
    item_id: str,
    repeat: int,
    findings: tuple[ReviewFinding, ...],
    verdict: ReviewVerdict = ReviewVerdict.CHANGES_REQUESTED,
) -> EvalRun:
    """Build a comparable run record for a metric test.

    Args:
        config_id: Config the run belongs to.
        item_id: Corpus item the run reviewed.
        repeat: 1-based repeat index.
        findings: Findings the run reported.
        verdict: Derived verdict recorded for the run.

    Returns:
        A run record in the ``OK`` state.
    """
    return EvalRun(
        config_id=config_id,
        item_id=item_id,
        repeat=repeat,
        status=RunStatus.OK,
        verdict=verdict,
        findings=findings,
    )


def test_jaccard_index_of_identical_sets_is_one() -> None:
    """Identical fingerprint sets score a perfect Jaccard index."""
    findings = (make_finding(title="Off by one"),)
    prints = fingerprints_for(findings=findings)

    assert_that(jaccard_index(left=prints, right=prints)).is_equal_to(1.0)


def test_jaccard_index_of_disjoint_sets_is_zero() -> None:
    """Disjoint fingerprint sets score zero."""
    left = fingerprints_for(findings=(make_finding(title="Off by one"),))
    right = fingerprints_for(findings=(make_finding(title="Leaked handle"),))

    assert_that(jaccard_index(left=left, right=right)).is_equal_to(0.0)


def test_jaccard_index_of_two_empty_sets_is_one() -> None:
    """Two runs that both reported nothing agree perfectly."""
    empty: frozenset[str] = frozenset()

    assert_that(jaccard_index(left=empty, right=empty)).is_equal_to(1.0)


def test_jaccard_index_of_partial_overlap() -> None:
    """A one-of-two overlap scores one half."""
    shared = make_finding(title="Off by one")
    left = fingerprints_for(findings=(shared, make_finding(title="Leaked handle")))
    right = fingerprints_for(findings=(shared,))

    assert_that(jaccard_index(left=left, right=right)).is_equal_to(0.5)


def test_fingerprints_ignore_line_drift() -> None:
    """The same finding at a different line keeps one fingerprint."""
    early = make_finding(title="Off by one", line=4)
    late = make_finding(title="Off by one", line=91)

    assert_that(fingerprints_for(findings=(early, late))).is_length(1)


def test_finding_match_rate_uses_the_production_matcher() -> None:
    """Reworded prose at a drifted line still matches on fingerprint."""
    left = (make_finding(title="Off by one", line=4),)
    right = (make_finding(title="off  by  one", line=88),)

    assert_that(finding_match_rate(left=left, right=right)).is_equal_to(1.0)


def test_finding_match_rate_is_symmetric_on_partial_overlap() -> None:
    """One shared finding out of three scores two thirds of the total."""
    shared = make_finding(title="Off by one")
    left = (shared, make_finding(title="Leaked handle"))
    right = (shared,)

    rate = finding_match_rate(left=left, right=right)

    assert_that(rate).is_close_to(2 / 3, tolerance=1e-9)
    assert_that(finding_match_rate(left=right, right=left)).is_close_to(
        rate,
        tolerance=1e-9,
    )


def test_finding_match_rate_of_two_empty_sets_is_one() -> None:
    """Two silent runs match completely."""
    assert_that(finding_match_rate(left=(), right=())).is_equal_to(1.0)


def test_mean_or_none_of_empty_sample_is_none() -> None:
    """An empty sample has no mean rather than a fabricated zero."""
    assert_that(mean_or_none([])).is_none()


def test_config_stability_reports_a_clean_noise_floor() -> None:
    """Identical repeats give a zero flip rate and a Jaccard of one."""
    findings = (make_finding(title="Off by one"),)
    runs = [
        _run(config_id="a", item_id="pr-1", repeat=index, findings=findings)
        for index in (1, 2, 3)
    ]

    stability = config_stability(config_id="a", runs=runs)

    assert_that(stability.compared_pairs).is_equal_to(3)
    assert_that(stability.verdict_flip_rate).is_equal_to(0.0)
    assert_that(stability.mean_jaccard).is_equal_to(1.0)
    assert_that(stability.failed_runs).is_equal_to(0)


def test_config_stability_measures_verdict_flips() -> None:
    """A config that flips its verdict on one of three pairs is not stable."""
    blocking = (make_finding(title="Off by one", severity=Severity.P1),)
    nit = (make_finding(title="Off by one", severity=Severity.P3),)
    runs = [
        _run(
            config_id="a",
            item_id="pr-1",
            repeat=1,
            findings=blocking,
            verdict=ReviewVerdict.BLOCKED,
        ),
        _run(
            config_id="a",
            item_id="pr-1",
            repeat=2,
            findings=blocking,
            verdict=ReviewVerdict.BLOCKED,
        ),
        _run(
            config_id="a",
            item_id="pr-1",
            repeat=3,
            findings=nit,
            verdict=ReviewVerdict.NITS_ONLY,
        ),
    ]

    stability = config_stability(config_id="a", runs=runs)

    assert_that(stability.verdict_flip_rate).is_close_to(2 / 3, tolerance=1e-9)
    assert_that(stability.verdicts).contains(
        ReviewVerdict.BLOCKED,
        ReviewVerdict.NITS_ONLY,
    )


def test_config_stability_ignores_other_configs_and_failures() -> None:
    """Only the named config's comparable runs feed its noise floor."""
    findings = (make_finding(title="Off by one"),)
    runs = [
        _run(config_id="a", item_id="pr-1", repeat=1, findings=findings),
        _run(config_id="a", item_id="pr-1", repeat=2, findings=findings),
        EvalRun(
            config_id="a",
            item_id="pr-1",
            repeat=3,
            status=RunStatus.FAILED,
            error="boom",
        ),
        _run(config_id="b", item_id="pr-1", repeat=1, findings=()),
    ]

    stability = config_stability(config_id="a", runs=runs)

    assert_that(stability.compared_pairs).is_equal_to(1)
    assert_that(stability.failed_runs).is_equal_to(1)


def test_config_stability_never_compares_across_corpus_items() -> None:
    """Two different pull requests are not a repeat pair."""
    runs = [
        _run(
            config_id="a",
            item_id="pr-1",
            repeat=1,
            findings=(make_finding(title="Off by one"),),
        ),
        _run(
            config_id="a",
            item_id="pr-2",
            repeat=1,
            findings=(make_finding(title="Leaked handle"),),
        ),
    ]

    stability = config_stability(config_id="a", runs=runs)

    assert_that(stability.compared_pairs).is_equal_to(0)
    assert_that(stability.verdict_flip_rate).is_none()
    assert_that(stability.mean_jaccard).is_none()


def test_cross_config_agreement_reports_both_noise_floors() -> None:
    """Agreement always travels with the two configs' own flip rates."""
    findings = (make_finding(title="Off by one"),)
    runs = [
        _run(config_id="a", item_id="pr-1", repeat=1, findings=findings),
        _run(config_id="a", item_id="pr-1", repeat=2, findings=findings),
        _run(config_id="b", item_id="pr-1", repeat=1, findings=findings),
        _run(config_id="b", item_id="pr-1", repeat=2, findings=findings),
    ]
    left = config_stability(config_id="a", runs=runs)
    right = config_stability(config_id="b", runs=runs)

    agreement = cross_config_agreement(left=left, right=right, runs=runs)

    assert_that(agreement.compared_pairs).is_equal_to(4)
    assert_that(agreement.finding_match_rate).is_equal_to(1.0)
    assert_that(agreement.verdict_agreement).is_equal_to(1.0)
    assert_that(agreement.left_noise_floor).is_equal_to(0.0)
    assert_that(agreement.right_noise_floor).is_equal_to(0.0)


def test_cross_config_agreement_scores_disjoint_configs_at_zero() -> None:
    """Two configs that share no finding agree on nothing."""
    runs = [
        _run(
            config_id="a",
            item_id="pr-1",
            repeat=1,
            findings=(make_finding(title="Off by one"),),
            verdict=ReviewVerdict.BLOCKED,
        ),
        _run(
            config_id="b",
            item_id="pr-1",
            repeat=1,
            findings=(make_finding(title="Leaked handle"),),
            verdict=ReviewVerdict.NITS_ONLY,
        ),
    ]
    left = config_stability(config_id="a", runs=runs)
    right = config_stability(config_id="b", runs=runs)

    agreement = cross_config_agreement(left=left, right=right, runs=runs)

    assert_that(agreement.finding_match_rate).is_equal_to(0.0)
    assert_that(agreement.mean_jaccard).is_equal_to(0.0)
    assert_that(agreement.verdict_agreement).is_equal_to(0.0)
    assert_that(agreement.left_noise_floor).is_none()


def test_cross_config_agreement_needs_a_shared_corpus_item() -> None:
    """Configs run on different items produce no comparable pair."""
    runs = [
        _run(config_id="a", item_id="pr-1", repeat=1, findings=()),
        _run(config_id="b", item_id="pr-2", repeat=1, findings=()),
    ]
    left = config_stability(config_id="a", runs=runs)
    right = config_stability(config_id="b", runs=runs)

    agreement = cross_config_agreement(left=left, right=right, runs=runs)

    assert_that(agreement.compared_pairs).is_equal_to(0)
    assert_that(agreement.finding_match_rate).is_none()


def _labeled_corpus() -> Corpus:
    """Return a one-item corpus carrying two labels.

    Returns:
        A corpus whose single item expects two findings.
    """
    return Corpus(
        version=1,
        items=(
            CorpusItem(
                item_id="pr-1",
                repo="lgtm-hq/py-lintro",
                pr=1,
                labeled_findings=(
                    LabeledFinding(
                        file="lintro/example.py",
                        category="correctness",
                        title="Off by one",
                        severity=Severity.P1,
                    ),
                    LabeledFinding(
                        file="lintro/example.py",
                        category="correctness",
                        title="Leaked handle",
                        severity=Severity.P2,
                    ),
                ),
            ),
        ),
    )


def test_efficacy_scores_a_perfect_config() -> None:
    """Reporting exactly the labels gives precision and recall of one."""
    corpus = _labeled_corpus()
    runs = [
        _run(
            config_id="a",
            item_id="pr-1",
            repeat=1,
            findings=(
                make_finding(title="Off by one"),
                make_finding(title="Leaked handle"),
            ),
        ),
    ]

    efficacy = efficacy_against_labels(config_id="a", runs=runs, corpus=corpus)

    assert_that(efficacy.true_positives).is_equal_to(2)
    assert_that(efficacy.false_positives).is_equal_to(0)
    assert_that(efficacy.false_negatives).is_equal_to(0)
    assert_that(efficacy.precision).is_equal_to(1.0)
    assert_that(efficacy.recall).is_equal_to(1.0)
    assert_that(efficacy.f1).is_equal_to(1.0)


def test_efficacy_counts_misses_and_spurious_findings() -> None:
    """One hit, one miss and one invention give a half precision and recall."""
    corpus = _labeled_corpus()
    runs = [
        _run(
            config_id="a",
            item_id="pr-1",
            repeat=1,
            findings=(
                make_finding(title="Off by one"),
                make_finding(title="Style nit"),
            ),
        ),
    ]

    efficacy = efficacy_against_labels(config_id="a", runs=runs, corpus=corpus)

    assert_that(efficacy.true_positives).is_equal_to(1)
    assert_that(efficacy.false_positives).is_equal_to(1)
    assert_that(efficacy.false_negatives).is_equal_to(1)
    assert_that(efficacy.precision).is_equal_to(0.5)
    assert_that(efficacy.recall).is_equal_to(0.5)
    assert_that(efficacy.f1).is_equal_to(0.5)


def test_efficacy_skips_unlabeled_items() -> None:
    """Runs over unlabeled items never enter the precision/recall counts."""
    corpus = Corpus(
        version=1,
        items=(CorpusItem(item_id="pr-2", repo="lgtm-hq/py-lintro", pr=2),),
    )
    runs = [
        _run(
            config_id="a",
            item_id="pr-2",
            repeat=1,
            findings=(make_finding(title="Off by one"),),
        ),
    ]

    efficacy = efficacy_against_labels(config_id="a", runs=runs, corpus=corpus)

    assert_that(efficacy.labeled_runs).is_equal_to(0)
    assert_that(efficacy.precision).is_none()
    assert_that(efficacy.recall).is_none()
    assert_that(efficacy.f1).is_none()
