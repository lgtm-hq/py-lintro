"""Result models for the stability, agreement and efficacy metrics."""

from __future__ import annotations

from dataclasses import dataclass, field

from lintro.ai.review.enums.review_verdict import ReviewVerdict
from review_matrix.models.run import EvalRun

__all__ = [
    "AgreementMetrics",
    "EfficacyMetrics",
    "MatrixReport",
    "StabilityMetrics",
]


@dataclass(frozen=True, slots=True)
class StabilityMetrics:
    """Run-to-run stability of one config: its noise floor.

    Attributes:
        config_id: Matrix config the metrics describe.
        compared_pairs: Number of same-item repeat pairs that were compared.
            Zero means the config produced fewer than two comparable runs for
            every item, and both rates below are ``None``.
        verdict_flip_rate: Fraction of repeat pairs whose derived verdicts
            disagree, averaged per item then across items. ``None`` when
            nothing was comparable.
        mean_jaccard: Mean finding-set Jaccard across repeat pairs, averaged
            per item then across items. ``None`` when nothing was comparable.
        verdicts: Every derived verdict observed, sorted, for the report.
        failed_runs: Number of runs that never became comparable — failed,
            unparseable, or incomplete.
    """

    config_id: str
    compared_pairs: int = 0
    verdict_flip_rate: float | None = None
    mean_jaccard: float | None = None
    verdicts: tuple[ReviewVerdict, ...] = field(default_factory=tuple)
    failed_runs: int = 0


@dataclass(frozen=True, slots=True)
class AgreementMetrics:
    """Cross-config agreement for one unordered pair of configs.

    Attributes:
        left_config_id: First config of the pair (alphabetically first).
        right_config_id: Second config of the pair.
        compared_pairs: Number of cross-config run pairs compared.
        finding_match_rate: Mean symmetric match rate over cross pairs, where
            a pair's rate is ``2 * matched / (left_total + right_total)`` and
            ``matched`` comes from the production finding matcher.
        mean_jaccard: Mean fingerprint-set Jaccard over cross pairs.
        verdict_agreement: Fraction of cross pairs whose derived verdicts are
            equal.
        left_noise_floor: ``left``'s own verdict flip rate, carried here so an
            agreement number is never read without its noise floor.
        right_noise_floor: ``right``'s own verdict flip rate.
    """

    left_config_id: str
    right_config_id: str
    compared_pairs: int = 0
    finding_match_rate: float | None = None
    mean_jaccard: float | None = None
    verdict_agreement: float | None = None
    left_noise_floor: float | None = None
    right_noise_floor: float | None = None


@dataclass(frozen=True, slots=True)
class EfficacyMetrics:
    """Precision and recall of one config against the labeled corpus.

    Attributes:
        config_id: Matrix config the metrics describe.
        labeled_runs: Number of comparable runs over labeled corpus items.
        true_positives: Reported findings that matched a label.
        false_positives: Reported findings with no matching label.
        false_negatives: Labels a comparable run did not report, pooled over
            every comparable run of every labeled item. One label missed by
            three runs counts three times, the same way ``true_positives``
            counts a label found three times.
        precision: ``tp / (tp + fp)``; ``None`` when nothing was reported.
        recall: ``tp / (tp + fn)``; ``None`` when no labels applied.
        f1: Harmonic mean of precision and recall; ``None`` when either is
            ``None`` or both are zero.
    """

    config_id: str
    labeled_runs: int = 0
    true_positives: int = 0
    false_positives: int = 0
    false_negatives: int = 0
    precision: float | None = None
    recall: float | None = None
    f1: float | None = None


@dataclass(frozen=True, slots=True)
class MatrixReport:
    """Everything one matrix run measured.

    Attributes:
        matrix_version: Schema version of the matrix file used.
        corpus_version: Schema version of the corpus file used.
        repeats: Repeats per (config, item) cell requested by the matrix.
        config_ids: Config ids in matrix order.
        item_ids: Corpus item ids in corpus order.
        runs: Every persisted run, in execution order.
        stability: Per-config noise floors, in matrix order.
        agreement: Cross-config agreement, one entry per unordered config
            pair, sorted by config id.
        efficacy: Per-config precision/recall, in matrix order. Empty when the
            corpus carries no labels.
        total_cost_usd: Sum of every run's *known* cost. Runs whose cost
            could not be read are excluded, not counted as zero.
        unknown_cost_runs: How many runs recorded no readable cost, so the
            total above is never mistaken for the complete spend.
    """

    matrix_version: int
    corpus_version: int
    repeats: int
    config_ids: tuple[str, ...] = field(default_factory=tuple)
    item_ids: tuple[str, ...] = field(default_factory=tuple)
    runs: tuple[EvalRun, ...] = field(default_factory=tuple)
    stability: tuple[StabilityMetrics, ...] = field(default_factory=tuple)
    agreement: tuple[AgreementMetrics, ...] = field(default_factory=tuple)
    efficacy: tuple[EfficacyMetrics, ...] = field(default_factory=tuple)
    total_cost_usd: float = 0.0
    unknown_cost_runs: int = 0
