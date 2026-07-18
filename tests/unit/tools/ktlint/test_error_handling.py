"""Unit tests for KtlintPlugin error and timeout handling."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

from assertpy import assert_that

from lintro.tools.definitions.ktlint import KtlintPlugin

from .conftest import error, make_result


def _write_kt(tmp_path: Path) -> Path:
    """Create a Kotlin source file for discovery.

    Args:
        tmp_path: Temporary directory.

    Returns:
        Path to the created file.
    """
    kt_file = tmp_path / "Example.kt"
    kt_file.write_text("class Example\n")
    return kt_file


def test_check_timeout_returns_failure(
    ktlint_plugin: KtlintPlugin,
    tmp_path: Path,
) -> None:
    """A subprocess timeout during check yields a failure result.

    Args:
        ktlint_plugin: The plugin under test.
        tmp_path: Temporary directory path.
    """
    kt_file = _write_kt(tmp_path)

    with patch.object(
        ktlint_plugin,
        "_run_subprocess_result",
        side_effect=subprocess.TimeoutExpired(cmd="ktlint", timeout=60),
    ):
        result = ktlint_plugin.check([str(kt_file)], {})

    assert_that(result.success).is_false()
    assert_that(result.output).contains("timed out")


def test_fix_timeout_returns_failure(
    ktlint_plugin: KtlintPlugin,
    tmp_path: Path,
) -> None:
    """A subprocess timeout during fix yields a failure result.

    Args:
        ktlint_plugin: The plugin under test.
        tmp_path: Temporary directory path.
    """
    kt_file = _write_kt(tmp_path)

    with patch.object(
        ktlint_plugin,
        "_run_subprocess_result",
        side_effect=subprocess.TimeoutExpired(cmd="ktlint", timeout=60),
    ):
        result = ktlint_plugin.fix([str(kt_file)], {})

    assert_that(result.success).is_false()
    assert_that(result.output).contains("timed out")


def test_fix_execution_failure_before_format(
    ktlint_plugin: KtlintPlugin,
    tmp_path: Path,
) -> None:
    """A failed initial check aborts the fix before running --format.

    Args:
        ktlint_plugin: The plugin under test.
        tmp_path: Temporary directory path.
    """
    kt_file = _write_kt(tmp_path)

    with patch.object(
        ktlint_plugin,
        "_run_subprocess_result",
        side_effect=[make_result(None)],
    ) as mock_run:
        result = ktlint_plugin.fix([str(kt_file)], {})

    # Only the initial check should have run; no --format, no re-check.
    assert_that(mock_run.call_count).is_equal_to(1)
    assert_that(result.success).is_false()
    assert_that(result.output).contains("JAVA_HOME")


def test_fix_no_kotlin_files(
    ktlint_plugin: KtlintPlugin,
    tmp_path: Path,
) -> None:
    """Fix with no Kotlin files returns success without invoking ktlint.

    Args:
        ktlint_plugin: The plugin under test.
        tmp_path: Temporary directory path.
    """
    other = tmp_path / "notes.txt"
    other.write_text("not kotlin")

    result = ktlint_plugin.fix([str(other)], {})

    assert_that(result.success).is_true()
    assert_that(result.output).contains("No")
    assert_that(result.issues_count).is_equal_to(0)


def test_check_ignores_error_entries_that_are_not_dicts(
    ktlint_plugin: KtlintPlugin,
    tmp_path: Path,
) -> None:
    """Well-formed reports with a single valid error parse to one issue.

    Args:
        ktlint_plugin: The plugin under test.
        tmp_path: Temporary directory path.
    """
    kt_file = _write_kt(tmp_path)
    entries = [{"file": str(kt_file), "errors": [error("standard:filename")]}]

    with patch.object(
        ktlint_plugin,
        "_run_subprocess_result",
        return_value=make_result(entries),
    ):
        result = ktlint_plugin.check([str(kt_file)], {})

    assert_that(result.issues_count).is_equal_to(1)
