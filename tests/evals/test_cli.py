"""Tests for the harness entry point: run-directory reuse and exit codes.

Every invocation goes through an injected fake invoker, so no test here starts
a subprocess, reaches a provider, or touches the network.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from assertpy import assert_that
from review_matrix.cli import main
from review_matrix.invoker import InvocationResult, ReviewInvoker
from review_matrix.models.corpus import CorpusItem
from review_matrix.models.matrix import MatrixConfig, MatrixSpec
from review_matrix.runner import RUNS_JSONL_NAME

from tests.evals.helpers import make_payload

MATRIX_DOCUMENT = {
    "version": 1,
    "repeats": 1,
    "depth": 1,
    "timeout_seconds": 900,
    "configs": [
        {
            "id": "config-a",
            "provider": "anthropic",
            "model": "claude-sonnet-4-6",
            "transport": "api",
            "max_cost_usd": 1.0,
        },
    ],
}
CORPUS_DOCUMENT = {
    "version": 1,
    "repo": "lgtm-hq/py-lintro",
    "items": [{"id": "pr-1", "pr": 1}],
}


def _write_specs(tmp_path: Path) -> tuple[Path, Path]:
    """Write a one-cell matrix and a one-item corpus to disk.

    Args:
        tmp_path: Directory to write the spec files into.

    Returns:
        The matrix path and the corpus path.
    """
    matrix_path = tmp_path / "matrix.json"
    corpus_path = tmp_path / "corpus.json"
    matrix_path.write_text(json.dumps(MATRIX_DOCUMENT), encoding="utf-8")
    corpus_path.write_text(json.dumps(CORPUS_DOCUMENT), encoding="utf-8")
    return matrix_path, corpus_path


def _fake_invoker(stdout: str, *, exit_code: int = 0) -> ReviewInvoker:
    """Build an invoker that always returns the same canned result.

    Args:
        stdout: Payload text every invocation returns.
        exit_code: Exit code every invocation returns.

    Returns:
        A callable matching the ``ReviewInvoker`` protocol.
    """

    def _invoke(
        *,
        config: MatrixConfig,
        item: CorpusItem,
        spec: MatrixSpec,
    ) -> InvocationResult:
        """Return the canned result.

        Args:
            config: Matrix cell being exercised.
            item: Corpus item being reviewed.
            spec: Matrix specification.

        Returns:
            The canned invocation result.
        """
        del config, item, spec
        return InvocationResult(
            exit_code=exit_code,
            stdout=stdout,
            stderr="",
            elapsed_seconds=1.0,
        )

    return _invoke


def _run_cli(
    *,
    tmp_path: Path,
    stdout: str,
    extra_args: tuple[str, ...] = (),
    exit_code: int = 0,
) -> int:
    """Execute a confirmed matrix run through the CLI with a fake invoker.

    Args:
        tmp_path: Directory holding the spec files and the runs root.
        stdout: Payload text the fake invoker returns.
        extra_args: Additional command-line arguments.
        exit_code: Exit code the fake invoker returns.

    Returns:
        The CLI's exit code.
    """
    matrix_path, corpus_path = _write_specs(tmp_path)
    return main(
        [
            "--matrix",
            str(matrix_path),
            "--corpus",
            str(corpus_path),
            "--runs-root",
            str(tmp_path / "runs"),
            "--stamp",
            "run-1",
            "--confirm-spend",
            *extra_args,
        ],
        invoker=_fake_invoker(stdout, exit_code=exit_code),
    )


def test_cli_writes_both_reports_for_a_confirmed_run(tmp_path: Path) -> None:
    """A confirmed run exits 0 and leaves both report files behind.

    Args:
        tmp_path: Pytest temporary directory.
    """
    code = _run_cli(tmp_path=tmp_path, stdout=make_payload(titles=("Off by one",)))

    run_dir = tmp_path / "runs" / "run-1"
    assert_that(code).is_equal_to(0)
    assert_that((run_dir / "report.json").exists()).is_true()
    assert_that((run_dir / "report.md").exists()).is_true()


def test_cli_refuses_an_existing_run_directory(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A second run into the same stamp exits 2 rather than mixing two runs.

    Args:
        tmp_path: Pytest temporary directory.
        capsys: Pytest output capture fixture.
    """
    payload = make_payload(titles=("Off by one",))
    _run_cli(tmp_path=tmp_path, stdout=payload)
    stale = tmp_path / "runs" / "run-1" / "report.json"
    stale_text = stale.read_text(encoding="utf-8")

    code = _run_cli(tmp_path=tmp_path, stdout=payload)

    assert_that(code).is_equal_to(2)
    assert_that(capsys.readouterr().err).contains("--overwrite")
    assert_that(stale.read_text(encoding="utf-8")).is_equal_to(stale_text)


def test_cli_overwrite_clears_stale_payloads_and_reports(tmp_path: Path) -> None:
    """--overwrite removes the previous run's artifacts before executing.

    Args:
        tmp_path: Pytest temporary directory.
    """
    _run_cli(tmp_path=tmp_path, stdout=make_payload(titles=("Off by one",)))
    run_dir = tmp_path / "runs" / "run-1"
    orphan = run_dir / "config-a" / "pr-1" / "run-9.json"
    orphan.write_text("{}", encoding="utf-8")
    unrelated = run_dir / "notes.txt"
    unrelated.write_text("keep me", encoding="utf-8")

    code = _run_cli(
        tmp_path=tmp_path,
        stdout=make_payload(titles=("Different finding",)),
        extra_args=("--overwrite",),
    )

    payload = json.loads((run_dir / "config-a" / "pr-1" / "run-1.json").read_text())
    assert_that(code).is_equal_to(0)
    assert_that(payload["findings"][0]["title"]).is_equal_to("Different finding")
    assert_that(orphan.exists()).is_false()
    assert_that(unrelated.read_text(encoding="utf-8")).is_equal_to("keep me")


def test_cli_exits_one_when_nothing_is_comparable(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A run whose every cell failed reports failure through the exit code.

    Args:
        tmp_path: Pytest temporary directory.
        capsys: Pytest output capture fixture.
    """
    code = _run_cli(tmp_path=tmp_path, stdout="not json at all")

    assert_that(code).is_equal_to(1)
    assert_that(capsys.readouterr().err).contains("comparable")
    assert_that((tmp_path / "runs" / "run-1" / "report.json").exists()).is_true()


@pytest.mark.parametrize(
    "stamp",
    ["/absolute", "../escape", "nested/stamp", ".", "..", "trailing/"],
)
def test_cli_rejects_a_stamp_that_is_not_a_directory_name(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    stamp: str,
) -> None:
    """A stamp is joined onto the runs root, so it must be a plain name.

    Args:
        tmp_path: Pytest temporary directory.
        capsys: Pytest output capture fixture.
        stamp: Candidate stamp that must be refused.
    """
    matrix_path, corpus_path = _write_specs(tmp_path)

    code = main(
        [
            "--matrix",
            str(matrix_path),
            "--corpus",
            str(corpus_path),
            "--runs-root",
            str(tmp_path / "runs"),
            "--stamp",
            stamp,
            "--confirm-spend",
        ],
        invoker=_fake_invoker(make_payload(titles=("Off by one",))),
    )

    assert_that(code).is_equal_to(2)
    assert_that(capsys.readouterr().err).contains("--stamp")
    assert_that((tmp_path / "runs").exists()).is_false()


def test_cli_reports_a_missing_matrix_file(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A nonexistent spec path is an error message, never a traceback.

    Args:
        tmp_path: Pytest temporary directory.
        capsys: Pytest output capture fixture.
    """
    _, corpus_path = _write_specs(tmp_path)
    missing = tmp_path / "nope" / "matrix.yaml"

    code = main(["--matrix", str(missing), "--corpus", str(corpus_path)])

    assert_that(code).is_equal_to(2)
    assert_that(capsys.readouterr().err).contains("cannot read", str(missing))


def test_cli_overwrite_clears_the_stale_run_journal(tmp_path: Path) -> None:
    """--overwrite starts a fresh runs.jsonl instead of appending to the old one.

    Args:
        tmp_path: Pytest temporary directory.
    """
    payload = make_payload(titles=("Off by one",))
    _run_cli(tmp_path=tmp_path, stdout=payload)
    journal = tmp_path / "runs" / "run-1" / RUNS_JSONL_NAME
    first_lines = journal.read_text(encoding="utf-8").splitlines()

    _run_cli(tmp_path=tmp_path, stdout=payload, extra_args=("--overwrite",))

    assert_that(journal.read_text(encoding="utf-8").splitlines()).is_length(
        len(first_lines),
    )
