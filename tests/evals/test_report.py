"""Tests for report assembly and rendering."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from assertpy import assert_that
from review_matrix.cli import main
from review_matrix.enums.run_status import RunStatus
from review_matrix.models.corpus import Corpus, CorpusItem, LabeledFinding
from review_matrix.models.matrix import MatrixConfig, MatrixSpec
from review_matrix.models.run import EvalRun
from review_matrix.report import build_report, render_markdown, report_to_dict

from lintro.ai.review.enums.review_verdict import ReviewVerdict
from lintro.ai.review.models.review_finding import Severity
from tests.evals.helpers import make_finding

SPEC = MatrixSpec(
    version=1,
    repeats=2,
    depth=1,
    timeout_seconds=900.0,
    configs=(
        MatrixConfig(
            config_id="config-a",
            provider="anthropic",
            model="claude-opus-4-5",
            transport="api",
            max_cost_usd=3.0,
            projected_cost_usd=1.0,
        ),
        MatrixConfig(
            config_id="config-b",
            provider="cursor",
            model="grok-4.6",
            transport="cli",
            max_cost_usd=2.0,
            projected_cost_usd=0.5,
        ),
    ),
)


def _corpus(*, labeled: bool) -> Corpus:
    """Build a one-item corpus, optionally carrying a label.

    Args:
        labeled: Whether the item carries a ground-truth label.

    Returns:
        The corpus.
    """
    labels = (
        (
            LabeledFinding(
                file="lintro/example.py",
                category="correctness",
                title="Off by one",
                severity=Severity.P2,
            ),
        )
        if labeled
        else ()
    )
    return Corpus(
        version=1,
        items=(
            CorpusItem(
                item_id="pr-1",
                repo="lgtm-hq/py-lintro",
                pr=1,
                labeled_findings=labels,
            ),
        ),
    )


def _runs() -> tuple[EvalRun, ...]:
    """Build two runs per config over one corpus item.

    Returns:
        Four comparable runs, all reporting the same finding.
    """
    findings = (make_finding(title="Off by one"),)
    return tuple(
        EvalRun(
            config_id=config_id,
            item_id="pr-1",
            repeat=repeat,
            status=RunStatus.OK,
            verdict=ReviewVerdict.CHANGES_REQUESTED,
            findings=findings,
            elapsed_seconds=2.0,
            cost_usd=0.5,
            exit_code=0,
            output_path=f"{config_id}/pr-1/run-{repeat}.json",
        )
        for config_id in ("config-a", "config-b")
        for repeat in (1, 2)
    )


def test_build_report_covers_every_config_pair() -> None:
    """Agreement is reported once per unordered pair of configs."""
    report = build_report(spec=SPEC, corpus=_corpus(labeled=False), runs=_runs())

    assert_that(report.stability).is_length(2)
    assert_that(report.agreement).is_length(1)
    assert_that(report.agreement[0].left_config_id).is_equal_to("config-a")
    assert_that(report.agreement[0].right_config_id).is_equal_to("config-b")


def test_build_report_omits_efficacy_for_an_unlabeled_corpus() -> None:
    """Precision and recall are absent rather than fabricated at zero."""
    report = build_report(spec=SPEC, corpus=_corpus(labeled=False), runs=_runs())

    assert_that(report.efficacy).is_empty()


def test_build_report_includes_efficacy_for_a_labeled_corpus() -> None:
    """A labeled corpus produces one efficacy row per config."""
    report = build_report(spec=SPEC, corpus=_corpus(labeled=True), runs=_runs())

    assert_that(report.efficacy).is_length(2)
    assert_that(report.efficacy[0].precision).is_equal_to(1.0)


def test_build_report_totals_the_recorded_cost() -> None:
    """Total spend is the sum of the runs' own recorded costs."""
    report = build_report(spec=SPEC, corpus=_corpus(labeled=False), runs=_runs())

    assert_that(report.total_cost_usd).is_close_to(2.0, tolerance=1e-9)


def test_report_to_dict_is_json_serializable() -> None:
    """The JSON report encodes without a custom encoder."""
    report = build_report(spec=SPEC, corpus=_corpus(labeled=True), runs=_runs())

    encoded = json.dumps(report_to_dict(report=report), sort_keys=True)

    assert_that(json.loads(encoded)["repeats"]).is_equal_to(2)


def test_report_to_dict_records_each_run_identity() -> None:
    """Every run keeps its config, item, verdict and payload path."""
    report = build_report(spec=SPEC, corpus=_corpus(labeled=False), runs=_runs())

    payload = report_to_dict(report=report)

    first = payload["runs"][0]
    assert_that(first["config_id"]).is_equal_to("config-a")
    assert_that(first["verdict"]).is_equal_to("changes_requested")
    assert_that(first["output_path"]).is_equal_to("config-a/pr-1/run-1.json")
    assert_that(first["finding_count"]).is_equal_to(1)


def test_render_markdown_reports_agreement_next_to_noise_floors() -> None:
    """The agreement table always carries both configs' noise floors."""
    report = build_report(spec=SPEC, corpus=_corpus(labeled=True), runs=_runs())

    markdown = render_markdown(report=report)

    assert_that(markdown).contains("## Stability (noise floor)")
    assert_that(markdown).contains("## Cross-config agreement")
    assert_that(markdown).contains("A noise floor")
    assert_that(markdown).contains("## Efficacy vs labeled corpus")


def test_render_markdown_omits_the_efficacy_table_without_labels() -> None:
    """An unlabeled corpus renders no precision/recall section."""
    report = build_report(spec=SPEC, corpus=_corpus(labeled=False), runs=_runs())

    markdown = render_markdown(report=report)

    assert_that(markdown).does_not_contain("Efficacy")


def test_render_markdown_marks_unmeasurable_metrics_as_not_available() -> None:
    """A single run per config leaves the noise floor explicitly unmeasured."""
    runs = tuple(run for run in _runs() if run.repeat == 1)
    report = build_report(spec=SPEC, corpus=_corpus(labeled=False), runs=runs)

    markdown = render_markdown(report=report)

    assert_that(markdown).contains("n/a")


def test_cli_dry_run_executes_nothing(
    tmp_path: Path,
    capsys: object,
) -> None:
    """Without --confirm-spend the CLI prints a projection and writes nothing.

    Args:
        tmp_path: Pytest temporary directory.
        capsys: Pytest capture fixture, unused beyond suppressing output.
    """
    del capsys
    runs_root = tmp_path / "runs"
    harness = Path(__file__).resolve().parents[2] / "evals" / "review-efficacy"

    exit_code = main(
        [
            "--matrix",
            str(harness / "matrix.yaml"),
            "--corpus",
            str(harness / "corpus" / "corpus.yaml"),
            "--runs-root",
            str(runs_root),
        ],
    )

    assert_that(exit_code).is_equal_to(0)
    assert_that(runs_root.exists()).is_false()


def test_cli_reports_a_malformed_matrix(tmp_path: Path) -> None:
    """A bad matrix file exits 2 instead of raising.

    Args:
        tmp_path: Pytest temporary directory.
    """
    bad = tmp_path / "matrix.yaml"
    bad.write_text("configs: []\n", encoding="utf-8")

    exit_code = main(["--matrix", str(bad), "--corpus", str(bad)])

    assert_that(exit_code).is_equal_to(2)


def test_render_markdown_omits_the_agreement_table_for_one_config() -> None:
    """With a single config there is no pair to agree, so the section is gone.

    The table is omitted only here: two configs that shared no comparable run
    still get a table, of n/a rows.
    """
    spec = replace(SPEC, configs=(SPEC.configs[0],))

    report = build_report(spec=spec, corpus=_corpus(labeled=False), runs=_runs())

    assert_that(report.agreement).is_empty()
    assert_that(render_markdown(report=report)).does_not_contain(
        "Cross-config agreement",
    )


def test_report_to_dict_leaves_unmeasurable_rates_null() -> None:
    """An unmeasurable rate serializes as null, never as a fabricated 0.0."""
    single = EvalRun(
        config_id="config-a",
        item_id="pr-1",
        repeat=1,
        status=RunStatus.OK,
        verdict=ReviewVerdict.READY,
    )

    report = build_report(
        spec=SPEC,
        corpus=_corpus(labeled=False),
        runs=(single,),
    )
    payload = json.loads(json.dumps(report_to_dict(report=report)))

    stability = next(
        entry for entry in payload["stability"] if entry["config_id"] == "config-a"
    )
    assert_that(stability["compared_pairs"]).is_equal_to(0)
    assert_that(stability["verdict_flip_rate"]).is_none()
    assert_that(stability["mean_jaccard"]).is_none()


def test_render_markdown_labels_the_cost_as_a_floor_when_any_is_unknown() -> None:
    """An unreadable cost makes the total a floor, with an exact run count."""
    known = EvalRun(
        config_id="config-a",
        item_id="pr-1",
        repeat=1,
        status=RunStatus.OK,
        verdict=ReviewVerdict.READY,
        cost_usd=0.25,
    )
    unknown = EvalRun(
        config_id="config-a",
        item_id="pr-1",
        repeat=2,
        status=RunStatus.OK,
        verdict=ReviewVerdict.READY,
        cost_usd=None,
    )

    report = build_report(
        spec=SPEC,
        corpus=_corpus(labeled=False),
        runs=(known, unknown),
    )
    markdown = render_markdown(report=report)

    assert_that(report.unknown_cost_runs).is_equal_to(1)
    assert_that(markdown).contains(
        "Total recorded cost: at least $0.25 (1 run(s) recorded no readable cost)",
    )


def test_render_markdown_states_the_cost_plainly_when_all_are_known() -> None:
    """With every cost readable the total is exact, with no floor qualifier."""
    report = build_report(spec=SPEC, corpus=_corpus(labeled=False), runs=_runs())
    markdown = render_markdown(report=report)

    assert_that(report.unknown_cost_runs).is_equal_to(0)
    assert_that(markdown).contains("Total recorded cost: $")
    assert_that(markdown).does_not_contain("at least")
