"""Unit tests for how the trufflehog plugin drives ARG_MAX-safe batching.

The batching helpers themselves live in ``lintro.tools.core.argv_batching`` and
are covered by ``tests/unit/tools/core/test_argv_batching.py``. What matters
here is the plugin's use of them: that it issues one subprocess per batch and
aggregates the per-batch results correctly.
"""

from __future__ import annotations

import subprocess  # nosec B404 - subprocess is used to drive the tool/CLI under test; invocations use shell=False
from pathlib import Path
from unittest.mock import patch

import pytest
from assertpy import assert_that

from lintro.tools.trufflehog.definition import TrufflehogPlugin
from lintro.utils.result_reason import incomplete_reason
from tests.unit.tools.trufflehog.conftest import (
    make_subprocess_result,
    sample_finding_line,
)


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
        "lintro.tools.core.argv_batching.argv_byte_budget",
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
        "lintro.tools.core.argv_batching.argv_byte_budget",
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


def test_check_batch_timeout_reports_timed_out(
    trufflehog_plugin: TrufflehogPlugin,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A batch that times out marks the aggregated scan as timed out (#2400).

    Args:
        trufflehog_plugin: The plugin under test.
        tmp_path: Temporary directory path.
        monkeypatch: Pytest monkeypatch fixture.
    """
    monkeypatch.setattr(
        "lintro.tools.core.argv_batching.argv_byte_budget",
        lambda: 1,
    )
    good = tmp_path / "aaa_good.py"
    good.write_text("TOKEN = 'ghp_fake'\n")
    slow = tmp_path / "zzz_slow.py"
    slow.write_text("nothing = 1\n")

    def fake_run(cmd: list[str], **_kwargs: object) -> object:
        scanned = next(a for a in cmd if a.endswith(".py"))
        if scanned.endswith("zzz_slow.py"):
            raise subprocess.TimeoutExpired(cmd=cmd, timeout=1)
        return make_subprocess_result(
            stdout=sample_finding_line(file=scanned),
            returncode=0,
        )

    with patch.object(
        trufflehog_plugin,
        "_run_subprocess_result",
        side_effect=fake_run,
    ):
        result = trufflehog_plugin.check([str(good), str(slow)], {})

    assert_that(result.success).is_false()
    assert_that(result.timed_out).is_true()
    assert_that(result.issues_count).is_equal_to(1)
    assert_that(result.output).contains("timed out")


def test_check_timeout_and_failure_report_both_reasons(
    trufflehog_plugin: TrufflehogPlugin,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A timed-out batch next to a failed batch is reported as both (#2400).

    Args:
        trufflehog_plugin: The plugin under test.
        tmp_path: Temporary directory path.
        monkeypatch: Pytest monkeypatch fixture.
    """
    monkeypatch.setattr(
        "lintro.tools.core.argv_batching.argv_byte_budget",
        lambda: 1,
    )
    for name in ("aaa_good.py", "mmm_bad.py", "zzz_slow.py"):
        (tmp_path / name).write_text("value = 1\n")

    def fake_run(cmd: list[str], **_kwargs: object) -> object:
        scanned = next(a for a in cmd if a.endswith(".py"))
        if scanned.endswith("zzz_slow.py"):
            raise subprocess.TimeoutExpired(cmd=cmd, timeout=1)
        if scanned.endswith("mmm_bad.py"):
            return make_subprocess_result(stderr="fatal: boom", returncode=1)
        return make_subprocess_result(stdout=sample_finding_line(file=scanned))

    with patch.object(
        trufflehog_plugin,
        "_run_subprocess_result",
        side_effect=fake_run,
    ):
        result = trufflehog_plugin.check(
            [
                str(tmp_path / name)
                for name in ("aaa_good.py", "mmm_bad.py", "zzz_slow.py")
            ],
            {},
        )

    assert_that(result.timed_out).is_true()
    assert_that(result.execution_failed).is_true()
    assert_that(result.issues_count).is_equal_to(1)
    assert_that(result.output).contains(
        "1 batch(es) timed out and 1 batch(es) failed",
        "timed out",
        "fatal: boom",
    )
    # The one-line reason the digest and --advisory-only print names both.
    assert_that(incomplete_reason(result)).is_equal_to(
        "timed out and failed (1 batch(es) timed out and 1 batch(es) failed)",
    )
