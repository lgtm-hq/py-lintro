"""Unit tests for ARG_MAX-safe batching in the trufflehog plugin."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from assertpy import assert_that

from lintro.tools.definitions.trufflehog import (
    TrufflehogPlugin,
    _argv_byte_budget,
    _chunk_source_paths,
)
from tests.unit.tools.trufflehog.conftest import (
    make_subprocess_result,
    sample_finding_line,
)


def test_argv_byte_budget_is_positive() -> None:
    """The derived argv byte budget must be a usable positive number."""
    assert_that(_argv_byte_budget()).is_greater_than(0)


def test_chunk_single_batch_when_budget_fits() -> None:
    """Short paths under the budget stay in a single batch, in order."""
    paths = ["/a/one.py", "/a/two.py", "/a/three.py"]

    batches = _chunk_source_paths(paths, fixed_arg_bytes=0)

    assert_that(batches).is_length(1)
    assert_that(batches[0]).is_equal_to(paths)


def test_chunk_splits_at_budget_boundary() -> None:
    """Paths exceeding the per-command budget are split across batches.

    A tiny budget (forced by a huge fixed-arg cost) puts each path in its own
    batch while preserving order and totality — no path is dropped.
    """
    paths = [f"/repo/file_{i}.py" for i in range(5)]

    # A fixed-arg cost far above ARG_MAX collapses the budget to its floor of 1,
    # so every path lands in a batch of its own.
    batches = _chunk_source_paths(paths, fixed_arg_bytes=10**9)

    assert_that(batches).is_length(5)
    # Order and totality preserved.
    flattened = [p for batch in batches for p in batch]
    assert_that(flattened).is_equal_to(paths)
    for batch in batches:
        assert_that(batch).is_length(1)


def test_chunk_oversized_single_path_still_batched() -> None:
    """A single path larger than the budget is never silently dropped."""
    huge = "/" + ("x" * 5000) + ".py"

    batches = _chunk_source_paths([huge], fixed_arg_bytes=10**9)

    assert_that(batches).is_length(1)
    assert_that(batches[0]).is_equal_to([huge])


def test_chunk_groups_multiple_paths_per_budgeted_batch() -> None:
    """Paths pack into as few batches as the budget allows, boundary honored."""
    # Each path is 8 bytes + 1 NUL terminator = 9 bytes. With a fixed budget of
    # 20 (fixed_arg_bytes chosen so budget == 20), two paths (18 bytes) fit but
    # a third (27 bytes) overflows -> new batch.
    paths = ["/aa/b.py", "/cc/d.py", "/ee/f.py", "/gg/h.py"]
    budget_target = 20
    fixed = _argv_byte_budget() - budget_target

    batches = _chunk_source_paths(paths, fixed_arg_bytes=fixed)

    # 2 paths per batch (9 + 9 = 18 <= 20; adding a third 27 > 20).
    assert_that(batches).is_length(2)
    assert_that(batches[0]).is_length(2)
    assert_that(batches[1]).is_length(2)
    flattened = [p for batch in batches for p in batch]
    assert_that(flattened).is_equal_to(paths)


def test_check_batches_and_aggregates_findings(
    trufflehog_plugin: TrufflehogPlugin,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Findings from every batch are merged into one aggregated result.

    Args:
        trufflehog_plugin: The plugin under test.
        tmp_path: Temporary directory path.
        monkeypatch: Pytest monkeypatch fixture.
    """
    # Force one file per batch by shrinking the argv budget to its floor.
    monkeypatch.setattr(
        "lintro.tools.definitions.trufflehog._argv_byte_budget",
        lambda: 1,
    )
    files = []
    for i in range(3):
        f = tmp_path / f"config_{i}.py"
        f.write_text("TOKEN = 'ghp_fake'\n")
        files.append(f)

    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **_kwargs: object) -> object:
        calls.append(cmd)
        scanned = next(a for a in cmd if a.endswith(".py"))
        return make_subprocess_result(
            stdout=sample_finding_line(file=scanned),
            returncode=0,
        )

    with patch.object(
        trufflehog_plugin,
        "_run_subprocess_result",
        side_effect=fake_run,
    ):
        result = trufflehog_plugin.check([str(f) for f in files], {})

    assert_that(result.success).is_true()
    # One trufflehog invocation per batch.
    assert_that(calls).is_length(3)
    # Every batch's single finding is aggregated.
    assert_that(result.issues_count).is_equal_to(3)


def test_check_batch_failure_fails_overall_keeping_findings(
    trufflehog_plugin: TrufflehogPlugin,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One failing batch fails the whole scan while findings are preserved.

    Args:
        trufflehog_plugin: The plugin under test.
        tmp_path: Temporary directory path.
        monkeypatch: Pytest monkeypatch fixture.
    """
    monkeypatch.setattr(
        "lintro.tools.definitions.trufflehog._argv_byte_budget",
        lambda: 1,
    )
    good = tmp_path / "aaa_good.py"
    good.write_text("TOKEN = 'ghp_fake'\n")
    bad = tmp_path / "zzz_bad.py"
    bad.write_text("nothing = 1\n")

    def fake_run(cmd: list[str], **_kwargs: object) -> object:
        scanned = next(a for a in cmd if a.endswith(".py"))
        if scanned.endswith("zzz_bad.py"):
            return make_subprocess_result(
                stdout="",
                stderr="fatal: boom",
                returncode=1,
            )
        return make_subprocess_result(
            stdout=sample_finding_line(file=scanned),
            returncode=0,
        )

    with patch.object(
        trufflehog_plugin,
        "_run_subprocess_result",
        side_effect=fake_run,
    ):
        result = trufflehog_plugin.check([str(good), str(bad)], {})

    assert_that(result.success).is_false()
    # The finding from the successful batch survives aggregation.
    assert_that(result.issues_count).is_equal_to(1)
    assert_that(result.parse_failures_count).is_greater_than(0)
