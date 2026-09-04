"""Report assembly and rendering for one matrix run."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict
from itertools import combinations
from typing import Any

from review_matrix.metrics import (
    config_stability,
    cross_config_agreement,
    efficacy_against_labels,
)
from review_matrix.models.corpus import Corpus
from review_matrix.models.matrix import MatrixSpec
from review_matrix.models.metrics import EfficacyMetrics, MatrixReport
from review_matrix.models.run import EvalRun
from review_matrix.runner import run_to_dict, summarize_runs

__all__ = ["build_report", "render_markdown", "report_to_dict"]

#: Rendered in place of a metric that had nothing to measure.
NOT_AVAILABLE = "n/a"


def build_report(
    *,
    spec: MatrixSpec,
    corpus: Corpus,
    runs: Sequence[EvalRun],
) -> MatrixReport:
    """Compute every metric for one matrix run.

    Args:
        spec: Matrix specification that produced the runs.
        corpus: Corpus that was reviewed.
        runs: Every persisted run.

    Returns:
        The assembled report.
    """
    stability = tuple(
        config_stability(config_id=config.config_id, runs=runs)
        for config in spec.configs
    )
    by_id = {entry.config_id: entry for entry in stability}
    agreement = tuple(
        cross_config_agreement(left=by_id[left], right=by_id[right], runs=runs)
        for left, right in combinations(sorted(by_id), 2)
    )
    efficacy: tuple[EfficacyMetrics, ...] = ()
    if corpus.labeled_items:
        efficacy = tuple(
            efficacy_against_labels(
                config_id=config.config_id,
                runs=runs,
                corpus=corpus,
            )
            for config in spec.configs
        )
    return MatrixReport(
        matrix_version=spec.version,
        corpus_version=corpus.version,
        repeats=spec.repeats,
        config_ids=tuple(config.config_id for config in spec.configs),
        item_ids=tuple(item.item_id for item in corpus.items),
        runs=tuple(runs),
        stability=stability,
        agreement=agreement,
        efficacy=efficacy,
        total_cost_usd=summarize_runs(runs),
    )


def report_to_dict(*, report: MatrixReport) -> dict[str, Any]:
    """Serialize a report to a JSON-ready mapping.

    Each run is serialized by :func:`review_matrix.runner.run_to_dict`, the
    same function the incremental ``runs.jsonl`` journal uses, so the two can
    never describe one run differently.

    Args:
        report: Report to serialize.

    Returns:
        A mapping whose every value is JSON-encodable.
    """
    payload = asdict(report)
    payload["runs"] = [run_to_dict(run=run) for run in report.runs]
    for entry, source in zip(payload["stability"], report.stability, strict=True):
        entry["verdicts"] = [str(verdict) for verdict in source.verdicts]
    return payload


def _rate(value: float | None) -> str:
    """Format a rate for the markdown table.

    Args:
        value: Rate in ``[0, 1]``, or ``None`` when unmeasurable.

    Returns:
        Three-decimal string, or the not-available marker.
    """
    return NOT_AVAILABLE if value is None else f"{value:.3f}"


def _stability_table(report: MatrixReport) -> list[str]:
    """Render the per-config stability table.

    Args:
        report: Report being rendered.

    Returns:
        Markdown lines for the section.
    """
    lines = [
        "## Stability (noise floor)",
        "",
        "| config | pairs | verdict flip rate | mean Jaccard | non-comparable runs |",
        # The cell is StabilityMetrics.failed_runs, which counts every run
        # that never became comparable — failed, unparseable or incomplete
        # — not only the ones whose status is FAILED.
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    lines.extend(
        f"| {entry.config_id} | {entry.compared_pairs} "
        f"| {_rate(entry.verdict_flip_rate)} | {_rate(entry.mean_jaccard)} "
        f"| {entry.failed_runs} |"
        for entry in report.stability
    )
    return lines


def _agreement_table(report: MatrixReport) -> list[str]:
    """Render the cross-config agreement table.

    Args:
        report: Report being rendered.

    Returns:
        Markdown lines for the section; empty only when the matrix has fewer
        than two configs, since ``build_report`` emits one entry per unordered
        config pair. Two configs that shared no comparable run still get a
        table, of ``n/a`` rows.
    """
    if not report.agreement:
        return []
    lines = [
        "",
        "## Cross-config agreement",
        "",
        "Each row carries both configs' own noise floors, because an "
        "agreement number below the noise floor is not a difference between "
        "the configs.",
        "",
        "| config A | config B | pairs | match rate | Jaccard "
        "| verdict agreement | A noise floor | B noise floor |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    lines.extend(
        f"| {entry.left_config_id} | {entry.right_config_id} "
        f"| {entry.compared_pairs} | {_rate(entry.finding_match_rate)} "
        f"| {_rate(entry.mean_jaccard)} | {_rate(entry.verdict_agreement)} "
        f"| {_rate(entry.left_noise_floor)} | {_rate(entry.right_noise_floor)} |"
        for entry in report.agreement
    )
    return lines


def _efficacy_table(report: MatrixReport) -> list[str]:
    """Render the per-config efficacy table.

    Args:
        report: Report being rendered.

    Returns:
        Markdown lines for the section; empty when the corpus is unlabeled.
    """
    if not report.efficacy:
        return []
    lines = [
        "",
        "## Efficacy vs labeled corpus",
        "",
        "| config | runs | TP | FP | FN | precision | recall | F1 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    lines.extend(
        f"| {entry.config_id} | {entry.labeled_runs} | {entry.true_positives} "
        f"| {entry.false_positives} | {entry.false_negatives} "
        f"| {_rate(entry.precision)} | {_rate(entry.recall)} "
        f"| {_rate(entry.f1)} |"
        for entry in report.efficacy
    )
    return lines


def render_markdown(*, report: MatrixReport) -> str:
    """Render a report as a markdown document.

    Args:
        report: Report to render.

    Returns:
        The markdown document, newline-terminated.
    """
    lines = [
        "# Review agreement matrix",
        "",
        f"- Configs: {len(report.config_ids)}",
        f"- Corpus items: {len(report.item_ids)}",
        f"- Repeats per cell: {report.repeats}",
        f"- Runs recorded: {len(report.runs)}",
        f"- Total recorded cost: ${report.total_cost_usd:.2f}",
        "",
    ]
    lines.extend(_stability_table(report))
    lines.extend(_agreement_table(report))
    lines.extend(_efficacy_table(report))
    lines.append("")
    return "\n".join(lines)
