"""Tests for the matrix runner, its spend gate, and run persistence.

Every invocation here goes through an injected fake: no test in this module
starts a subprocess, reaches a provider, or touches the network.
"""

from __future__ import annotations

import json
from pathlib import Path

from assertpy import assert_that
from review_matrix.enums.run_status import RunStatus
from review_matrix.invoker import (
    InvocationResult,
    build_command,
    build_env,
)
from review_matrix.models.corpus import Corpus, CorpusItem
from review_matrix.models.matrix import MatrixConfig, MatrixSpec
from review_matrix.runner import (
    execute_matrix,
    plan_spend,
    render_spend_plan,
    summarize_runs,
)

from lintro.ai.config_overrides import ENV_MAX_COST_USD, ENV_PROVIDER
from lintro.ai.review.enums.review_verdict import ReviewVerdict
from tests.evals.helpers import make_payload

CONFIG_A = MatrixConfig(
    config_id="config-a",
    provider="anthropic",
    model="claude-opus-4-5",
    transport="api",
    max_cost_usd=3.0,
    projected_cost_usd=1.0,
)
CONFIG_B = MatrixConfig(
    config_id="config-b",
    provider="cursor",
    model="grok-4.6",
    transport="cli",
    max_cost_usd=2.0,
    projected_cost_usd=0.5,
)
SPEC = MatrixSpec(
    version=1,
    repeats=2,
    depth=1,
    timeout_seconds=900.0,
    configs=(CONFIG_A, CONFIG_B),
)
CORPUS = Corpus(
    version=1,
    items=(
        CorpusItem(item_id="pr-1", repo="lgtm-hq/py-lintro", pr=1),
        CorpusItem(item_id="pr-2", repo="lgtm-hq/py-lintro", pr=2),
    ),
)


class _RecordingInvoker:
    """Fake invoker returning canned payloads and recording its calls."""

    def __init__(self, *, stdout: str, exit_code: int = 0) -> None:
        """Store the canned response.

        Args:
            stdout: Payload text every invocation returns.
            exit_code: Exit code every invocation returns.
        """
        self.stdout = stdout
        self.exit_code = exit_code
        self.calls: list[tuple[str, str]] = []

    def __call__(
        self,
        *,
        config: MatrixConfig,
        item: CorpusItem,
        spec: MatrixSpec,
    ) -> InvocationResult:
        """Record the call and return the canned result.

        Args:
            config: Matrix cell being exercised.
            item: Corpus item being reviewed.
            spec: Matrix specification.

        Returns:
            The canned invocation result.
        """
        del spec
        self.calls.append((config.config_id, item.item_id))
        return InvocationResult(
            exit_code=self.exit_code,
            stdout=self.stdout,
            stderr="",
            elapsed_seconds=1.5,
        )


def test_plan_spend_multiplies_repeats_by_corpus_size() -> None:
    """The projection counts every cell of the matrix."""
    plan = plan_spend(spec=SPEC, corpus=CORPUS)

    assert_that(plan.total_runs).is_equal_to(8)
    assert_that(plan.projected_usd).is_close_to(6.0, tolerance=1e-9)
    assert_that(plan.ceiling_usd).is_close_to(20.0, tolerance=1e-9)


def test_plan_spend_breaks_down_per_config() -> None:
    """Each config's own projection and ceiling are reported separately."""
    plan = plan_spend(spec=SPEC, corpus=CORPUS)

    assert_that([entry.config_id for entry in plan.per_config]).is_equal_to(
        ["config-a", "config-b"],
    )
    assert_that(plan.per_config[0].runs).is_equal_to(4)
    assert_that(plan.per_config[0].ceiling_usd).is_close_to(12.0, tolerance=1e-9)


def test_unconfirmed_spend_plan_names_the_flag_that_would_execute() -> None:
    """A dry run says what it did not do and how to do it."""
    plan = plan_spend(spec=SPEC, corpus=CORPUS)

    rendered = render_spend_plan(plan=plan, confirmed=False)

    assert_that(rendered).contains("Dry run")
    assert_that(rendered).contains("--confirm-spend")


def test_confirmed_spend_plan_announces_execution() -> None:
    """A confirmed run says the matrix is about to execute."""
    plan = plan_spend(spec=SPEC, corpus=CORPUS)

    rendered = render_spend_plan(plan=plan, confirmed=True)

    assert_that(rendered).contains("executing the matrix")
    assert_that(rendered).does_not_contain("Dry run")


def test_build_command_pins_the_shared_review_knobs() -> None:
    """Depth, timeout, JSON output and no advisory tools are shared by all."""
    command = build_command(config=CONFIG_A, item=CORPUS.items[0], spec=SPEC)

    assert_that(list(command)).contains("--depth", "1", "--output", "json")
    assert_that(list(command)).contains("--advisory-tools", "none")
    assert_that(list(command)).contains("--pr", "1", "--repo", "lgtm-hq/py-lintro")


def test_build_command_carries_no_provider_flags() -> None:
    """Provider selection is env-only, never a CLI flag from the harness."""
    command = build_command(config=CONFIG_A, item=CORPUS.items[0], spec=SPEC)

    assert_that(list(command)).does_not_contain("--provider")
    assert_that(list(command)).does_not_contain("--model")
    assert_that(list(command)).does_not_contain("--transport")


def test_build_env_overlays_the_config_onto_the_base_environment() -> None:
    """The overlay adds the AI overrides and keeps everything else."""
    env = build_env(config=CONFIG_A, base_env={"PATH": "/usr/bin"})

    assert_that(env["PATH"]).is_equal_to("/usr/bin")
    assert_that(env[ENV_PROVIDER]).is_equal_to("anthropic")
    assert_that(env[ENV_MAX_COST_USD]).is_equal_to("3")


def test_execute_matrix_runs_every_cell(tmp_path: Path) -> None:
    """Every (config, item, repeat) triple is invoked exactly once."""
    invoker = _RecordingInvoker(stdout=make_payload(titles=("Off by one",)))

    runs = execute_matrix(
        spec=SPEC,
        corpus=CORPUS,
        output_dir=tmp_path,
        invoker=invoker,
    )

    assert_that(runs).is_length(8)
    assert_that(invoker.calls).is_length(8)
    assert_that(invoker.calls[0]).is_equal_to(("config-a", "pr-1"))


def test_execute_matrix_derives_the_verdict_from_the_findings(
    tmp_path: Path,
) -> None:
    """The recorded verdict comes from the matcher, not the payload label."""
    invoker = _RecordingInvoker(
        stdout=make_payload(titles=("Off by one",), severity="P3"),
    )

    runs = execute_matrix(
        spec=SPEC,
        corpus=CORPUS,
        output_dir=tmp_path,
        invoker=invoker,
    )

    assert_that(runs[0].verdict).is_equal_to(ReviewVerdict.NITS_ONLY)
    assert_that(runs[0].status).is_equal_to(RunStatus.OK)


def test_execute_matrix_persists_every_raw_payload(tmp_path: Path) -> None:
    """Raw review JSON is written under config/item/run paths."""
    invoker = _RecordingInvoker(stdout=make_payload(titles=("Off by one",)))

    runs = execute_matrix(
        spec=SPEC,
        corpus=CORPUS,
        output_dir=tmp_path,
        invoker=invoker,
    )

    written = tmp_path / runs[0].output_path
    assert_that(runs[0].output_path).is_equal_to("config-a/pr-1/run-1.json")
    assert_that(written.exists()).is_true()
    assert_that(written.read_text(encoding="utf-8")).contains("Off by one")


def test_execute_matrix_records_cost_and_elapsed(tmp_path: Path) -> None:
    """Cost comes from the payload and elapsed time from the invocation."""
    invoker = _RecordingInvoker(
        stdout=make_payload(titles=("Off by one",), cost_usd=0.75),
    )

    runs = execute_matrix(
        spec=SPEC,
        corpus=CORPUS,
        output_dir=tmp_path,
        invoker=invoker,
    )

    assert_that(runs[0].cost_usd).is_equal_to(0.75)
    assert_that(runs[0].elapsed_seconds).is_equal_to(1.5)
    assert_that(summarize_runs(runs)).is_close_to(6.0, tolerance=1e-9)


def test_execute_matrix_marks_a_failed_invocation(tmp_path: Path) -> None:
    """A non-zero exit with no payload is recorded, never silently dropped."""
    invoker = _RecordingInvoker(stdout="", exit_code=1)

    runs = execute_matrix(
        spec=SPEC,
        corpus=CORPUS,
        output_dir=tmp_path,
        invoker=invoker,
    )

    assert_that(runs[0].status).is_equal_to(RunStatus.FAILED)
    assert_that(runs[0].is_comparable).is_false()


def test_execute_matrix_marks_an_error_envelope_as_failed(tmp_path: Path) -> None:
    """An error envelope (exit 2, no review) is a failed cell, not a clean run."""
    envelope = json.dumps(
        {"error": {"kind": "auth_failed", "message": "no credential for anthropic"}},
    )
    invoker = _RecordingInvoker(stdout=envelope, exit_code=2)

    runs = execute_matrix(
        spec=SPEC,
        corpus=CORPUS,
        output_dir=tmp_path,
        invoker=invoker,
    )

    assert_that(runs[0].status).is_equal_to(RunStatus.FAILED)
    assert_that(runs[0].is_comparable).is_false()
    assert_that(runs[0].error).contains("auth_failed")
    assert_that(runs[0].findings).is_empty()


def test_execute_matrix_marks_a_payload_without_findings_as_failed(
    tmp_path: Path,
) -> None:
    """JSON that is not a review payload never scores as zero findings."""
    invoker = _RecordingInvoker(stdout=json.dumps({"metadata": {}}))

    runs = execute_matrix(
        spec=SPEC,
        corpus=CORPUS,
        output_dir=tmp_path,
        invoker=invoker,
    )

    assert_that(runs[0].status).is_equal_to(RunStatus.FAILED)
    assert_that(runs[0].error).contains("no findings list")


def test_execute_matrix_marks_unparseable_output(tmp_path: Path) -> None:
    """A clean exit with non-JSON stdout is not comparable either."""
    invoker = _RecordingInvoker(stdout="not json at all")

    runs = execute_matrix(
        spec=SPEC,
        corpus=CORPUS,
        output_dir=tmp_path,
        invoker=invoker,
    )

    assert_that(runs[0].status).is_equal_to(RunStatus.INVALID_OUTPUT)
    assert_that(runs[0].is_comparable).is_false()
