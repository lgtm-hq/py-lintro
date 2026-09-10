"""Stability, cross-config agreement and efficacy metrics.

Every comparison of two finding sets goes through
:mod:`review_matrix.findings`, which delegates to the production matcher. The
functions here only aggregate those comparisons.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from itertools import combinations

from lintro.ai.review.models.review_finding import ReviewFinding
from review_matrix.findings import (
    fingerprints_for,
    match_against,
    matched_count,
)
from review_matrix.models.corpus import Corpus, CorpusItem
from review_matrix.models.metrics import (
    AgreementMetrics,
    EfficacyMetrics,
    StabilityMetrics,
)
from review_matrix.models.run import EvalRun

__all__ = [
    "config_stability",
    "cross_config_agreement",
    "efficacy_against_labels",
    "finding_match_rate",
    "jaccard_index",
    "mean_or_none",
]


def jaccard_index(*, left: frozenset[str], right: frozenset[str]) -> float:
    """Return the Jaccard index of two fingerprint sets.

    Args:
        left: Fingerprints reported by one run.
        right: Fingerprints reported by the other run.

    Returns:
        ``|left & right| / |left | right|``. Two empty sets agree perfectly
        and score ``1.0``: a config that reports nothing twice is stable, even
        though it found nothing.
    """
    union = left | right
    if not union:
        return 1.0
    return len(left & right) / len(union)


def finding_match_rate(
    *,
    left: Sequence[ReviewFinding],
    right: Sequence[ReviewFinding],
) -> float:
    """Return the symmetric finding-level match rate of two finding sets.

    Matching is the production matcher's: two findings match when their
    ``(file, category, normalized title)`` fingerprints pair up, line drift and
    reworded prose included.

    Args:
        left: Findings from one run.
        right: Findings from the other run.

    Returns:
        ``2 * matched / (len(left) + len(right))``, or ``1.0`` when both sides
        reported nothing.
    """
    total = len(left) + len(right)
    if total == 0:
        return 1.0
    matched = matched_count(match_against(baseline=left, candidate=right))
    return (2 * matched) / total


def mean_or_none(values: Sequence[float]) -> float | None:
    """Return the mean of ``values``, or ``None`` when there are none.

    Args:
        values: Sample values.

    Returns:
        Arithmetic mean, or ``None`` for an empty sample.
    """
    if not values:
        return None
    return sum(values) / len(values)


def _by_item(runs: Sequence[EvalRun]) -> dict[str, list[EvalRun]]:
    """Group comparable runs by corpus item, preserving execution order.

    Args:
        runs: Runs to group.

    Returns:
        Mapping of item id to its comparable runs.
    """
    grouped: dict[str, list[EvalRun]] = defaultdict(list)
    for run in runs:
        if run.is_comparable:
            grouped[run.item_id].append(run)
    return dict(grouped)


def config_stability(
    *,
    config_id: str,
    runs: Sequence[EvalRun],
) -> StabilityMetrics:
    """Measure one config's run-to-run noise floor.

    Repeats are only ever compared within the same corpus item: two reviews of
    different pull requests disagreeing says nothing about stability. Per-item
    rates are averaged with equal weight so a pull request with more successful
    repeats cannot dominate the floor.

    Args:
        config_id: Config whose runs these are.
        runs: Every run recorded for that config, failures included.

    Returns:
        The config's stability metrics.
    """
    own = [run for run in runs if run.config_id == config_id]
    grouped = _by_item(own)
    item_flip_rates: list[float] = []
    item_jaccards: list[float] = []
    compared_pairs = 0
    for item_runs in grouped.values():
        flips: list[float] = []
        jaccards: list[float] = []
        for first, second in combinations(item_runs, 2):
            compared_pairs += 1
            flips.append(0.0 if first.verdict == second.verdict else 1.0)
            jaccards.append(
                jaccard_index(
                    left=fingerprints_for(findings=first.findings),
                    right=fingerprints_for(findings=second.findings),
                ),
            )
        item_flip = mean_or_none(flips)
        item_jaccard = mean_or_none(jaccards)
        if item_flip is not None:
            item_flip_rates.append(item_flip)
        if item_jaccard is not None:
            item_jaccards.append(item_jaccard)
    verdicts = sorted(
        {run.verdict for run in own if run.verdict is not None},
        key=str,
    )
    return StabilityMetrics(
        config_id=config_id,
        compared_pairs=compared_pairs,
        verdict_flip_rate=mean_or_none(item_flip_rates),
        mean_jaccard=mean_or_none(item_jaccards),
        verdicts=tuple(verdicts),
        failed_runs=sum(1 for run in own if not run.is_comparable),
    )


def cross_config_agreement(
    *,
    left: StabilityMetrics,
    right: StabilityMetrics,
    runs: Sequence[EvalRun],
) -> AgreementMetrics:
    """Measure agreement between two configs, next to their noise floors.

    Every cross pair of runs on the same corpus item is compared, and the
    per-item means are averaged with equal weight — the same shape as
    :func:`config_stability`, so the agreement number and the noise floor it
    sits beside are directly comparable.

    Args:
        left: Stability metrics of the first config; supplies its noise floor.
        right: Stability metrics of the second config.
        runs: Every run recorded for the whole matrix.

    Returns:
        The pair's agreement metrics.
    """
    left_runs = _by_item([run for run in runs if run.config_id == left.config_id])
    right_runs = _by_item([run for run in runs if run.config_id == right.config_id])
    item_match: list[float] = []
    item_jaccard: list[float] = []
    item_verdict: list[float] = []
    compared_pairs = 0
    for item_id in sorted(set(left_runs) & set(right_runs)):
        matches: list[float] = []
        jaccards: list[float] = []
        verdicts: list[float] = []
        for left_run in left_runs[item_id]:
            for right_run in right_runs[item_id]:
                compared_pairs += 1
                matches.append(
                    finding_match_rate(
                        left=left_run.findings,
                        right=right_run.findings,
                    ),
                )
                jaccards.append(
                    jaccard_index(
                        left=fingerprints_for(findings=left_run.findings),
                        right=fingerprints_for(findings=right_run.findings),
                    ),
                )
                verdicts.append(
                    1.0 if left_run.verdict == right_run.verdict else 0.0,
                )
        for sample, sink in (
            (matches, item_match),
            (jaccards, item_jaccard),
            (verdicts, item_verdict),
        ):
            mean = mean_or_none(sample)
            if mean is not None:
                sink.append(mean)
    return AgreementMetrics(
        left_config_id=left.config_id,
        right_config_id=right.config_id,
        compared_pairs=compared_pairs,
        finding_match_rate=mean_or_none(item_match),
        mean_jaccard=mean_or_none(item_jaccard),
        verdict_agreement=mean_or_none(item_verdict),
        left_noise_floor=left.verdict_flip_rate,
        right_noise_floor=right.verdict_flip_rate,
    )


def efficacy_against_labels(
    *,
    config_id: str,
    runs: Sequence[EvalRun],
    corpus: Corpus,
) -> EfficacyMetrics:
    """Measure precision and recall of one config against the labeled corpus.

    Counts are pooled across every comparable run of every labeled item, so a
    config that only sometimes reports a labeled finding is scored on how often
    it did, not on its luckiest run. Questions are dropped from the reported
    side first: a question asks about the diff rather than asserting a defect,
    so an unlabeled one is not a false positive.

    Args:
        config_id: Config whose runs these are.
        runs: Every run recorded for the whole matrix.
        corpus: Corpus supplying the ground-truth labels.

    Returns:
        The config's efficacy metrics. With an unlabeled corpus the counts are
        zero and ``precision``/``recall``/``f1`` are ``None`` (rendered
        ``n/a``), never ``0.0``: nothing was measured, which is not the same
        as having measured zero.
    """
    labels_by_item: dict[str, CorpusItem] = {
        item.item_id: item for item in corpus.labeled_items
    }
    true_positives = 0
    false_positives = 0
    false_negatives = 0
    labeled_runs = 0
    for run in runs:
        if run.config_id != config_id or not run.is_comparable:
            continue
        item = labels_by_item.get(run.item_id)
        if item is None:
            continue
        labeled_runs += 1
        expected = [label.to_finding() for label in item.labeled_findings]
        # Questions are not claims about the diff, so an unlabeled question is
        # not a false positive. ``derive_verdict`` excludes them from the
        # verdict for the same reason.
        claimed = [finding for finding in run.findings if not finding.is_question]
        result = match_against(baseline=expected, candidate=claimed)
        true_positives += matched_count(result)
        false_positives += len(result.new)
        false_negatives += len(result.resolved)
    reported = true_positives + false_positives
    relevant = true_positives + false_negatives
    precision = true_positives / reported if reported else None
    recall = true_positives / relevant if relevant else None
    f1: float | None = None
    if precision is not None and recall is not None and (precision + recall) > 0:
        f1 = (2 * precision * recall) / (precision + recall)
    return EfficacyMetrics(
        config_id=config_id,
        labeled_runs=labeled_runs,
        true_positives=true_positives,
        false_positives=false_positives,
        false_negatives=false_negatives,
        precision=precision,
        recall=recall,
        f1=f1,
    )
