"""Unit tests for KtlintPlugin check and fix execution."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from assertpy import assert_that

from lintro.tools.definitions.ktlint import KtlintPlugin

from .conftest import error, make_result


def _write_kt(tmp_path: Path, name: str = "Example.kt") -> Path:
    """Create a Kotlin source file for discovery.

    Args:
        tmp_path: Temporary directory.
        name: File name to create.

    Returns:
        Path to the created file.
    """
    kt_file = tmp_path / name
    kt_file.write_text('class Example {\n    fun greet() : String = "hi"\n}\n')
    return kt_file


def test_check_clean_file_reports_success(
    ktlint_plugin: KtlintPlugin,
    tmp_path: Path,
) -> None:
    """A clean run (empty report, exit 0) reports success and no issues.

    Args:
        ktlint_plugin: The plugin under test.
        tmp_path: Temporary directory path.
    """
    kt_file = _write_kt(tmp_path)

    with patch.object(
        ktlint_plugin,
        "_run_subprocess_result",
        return_value=make_result([]),
    ):
        result = ktlint_plugin.check([str(kt_file)], {})

    assert_that(result.success).is_true()
    assert_that(result.issues_count).is_equal_to(0)


def test_check_reports_issues(
    ktlint_plugin: KtlintPlugin,
    tmp_path: Path,
) -> None:
    """Issues found by ktlint are surfaced on the result.

    Args:
        ktlint_plugin: The plugin under test.
        tmp_path: Temporary directory path.
    """
    kt_file = _write_kt(tmp_path)
    entries = [
        {
            "file": str(kt_file),
            "errors": [error("standard:colon-spacing"), error("standard:op-spacing")],
        },
    ]

    with patch.object(
        ktlint_plugin,
        "_run_subprocess_result",
        return_value=make_result(entries),
    ):
        result = ktlint_plugin.check([str(kt_file)], {})

    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_equal_to(2)
    assert_that(result.issues[0].rule).is_equal_to("standard:colon-spacing")


def test_check_execution_failure_is_surfaced(
    ktlint_plugin: KtlintPlugin,
    tmp_path: Path,
) -> None:
    """A non-zero exit with no parseable report is treated as a failure.

    Args:
        ktlint_plugin: The plugin under test.
        tmp_path: Temporary directory path.
    """
    kt_file = _write_kt(tmp_path)

    with patch.object(
        ktlint_plugin,
        "_run_subprocess_result",
        return_value=make_result(None),
    ):
        result = ktlint_plugin.check([str(kt_file)], {})

    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_equal_to(1)
    assert_that(result.output).contains("JAVA_HOME")


def test_check_no_kotlin_files(
    ktlint_plugin: KtlintPlugin,
    tmp_path: Path,
) -> None:
    """No Kotlin files means success without invoking ktlint.

    Args:
        ktlint_plugin: The plugin under test.
        tmp_path: Temporary directory path.
    """
    other = tmp_path / "notes.txt"
    other.write_text("not kotlin")

    result = ktlint_plugin.check([str(other)], {})

    assert_that(result.success).is_true()
    assert_that(result.output).contains("No")


def test_fix_preserves_invariant_full_fix(
    ktlint_plugin: KtlintPlugin,
    tmp_path: Path,
) -> None:
    """When all issues are fixed, initial == fixed and remaining == 0.

    Args:
        ktlint_plugin: The plugin under test.
        tmp_path: Temporary directory path.
    """
    kt_file = _write_kt(tmp_path)
    initial = [
        {
            "file": str(kt_file),
            "errors": [error("standard:colon-spacing"), error("standard:op-spacing")],
        },
    ]

    # check (2 issues) -> format (no report used) -> re-check (clean)
    with patch.object(
        ktlint_plugin,
        "_run_subprocess_result",
        side_effect=[
            make_result(initial),
            make_result([]),
            make_result([]),
        ],
    ):
        result = ktlint_plugin.fix([str(kt_file)], {})

    assert_that(result.success).is_true()
    assert_that(result.initial_issues_count).is_equal_to(2)
    assert_that(result.fixed_issues_count).is_equal_to(2)
    assert_that(result.remaining_issues_count).is_equal_to(0)
    assert_that(
        result.initial_issues_count,
    ).is_equal_to(result.fixed_issues_count + result.remaining_issues_count)


def test_fix_preserves_invariant_partial_fix(
    ktlint_plugin: KtlintPlugin,
    tmp_path: Path,
) -> None:
    """A non-auto-correctable rule remains and the invariant still holds.

    Args:
        ktlint_plugin: The plugin under test.
        tmp_path: Temporary directory path.
    """
    kt_file = _write_kt(tmp_path)
    initial = [
        {
            "file": str(kt_file),
            "errors": [
                error("standard:filename"),
                error("standard:colon-spacing"),
                error("standard:op-spacing"),
            ],
        },
    ]
    remaining = [{"file": str(kt_file), "errors": [error("standard:filename")]}]

    with patch.object(
        ktlint_plugin,
        "_run_subprocess_result",
        side_effect=[
            make_result(initial),
            make_result(remaining),
            make_result(remaining),
        ],
    ):
        result = ktlint_plugin.fix([str(kt_file)], {})

    assert_that(result.success).is_false()
    assert_that(result.initial_issues_count).is_equal_to(3)
    assert_that(result.fixed_issues_count).is_equal_to(2)
    assert_that(result.remaining_issues_count).is_equal_to(1)
    assert_that(result.issues[0].rule).is_equal_to("standard:filename")
    assert_that(
        result.initial_issues_count,
    ).is_equal_to(result.fixed_issues_count + result.remaining_issues_count)
