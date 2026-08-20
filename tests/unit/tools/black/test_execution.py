"""Unit tests for black plugin check/fix execution."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from assertpy import assert_that

from lintro.tools.definitions.black import BlackPlugin

# Tests for BlackPlugin.check method


def test_check_clean_file(black_plugin: BlackPlugin, tmp_path: Path) -> None:
    """Check reports success when file is already formatted.

    Args:
        black_plugin: The BlackPlugin instance to test.
        tmp_path: Temporary directory path for test files.
    """
    py_file = tmp_path / "module.py"
    py_file.write_text('"""Module."""\n')

    with (
        patch.object(black_plugin, "_run_subprocess", return_value=(True, "")),
        patch.object(black_plugin, "_check_line_length_violations", return_value=[]),
    ):
        result = black_plugin.check([str(py_file)], {})

    assert_that(result.success).is_true()
    assert_that(result.issues_count).is_equal_to(0)


def test_check_needs_reformat(black_plugin: BlackPlugin, tmp_path: Path) -> None:
    """Check reports issues when black would reformat the file.

    Args:
        black_plugin: The BlackPlugin instance to test.
        tmp_path: Temporary directory path for test files.
    """
    py_file = tmp_path / "module.py"
    py_file.write_text("x=1\n")

    output = f"would reformat {py_file}\n"

    with (
        patch.object(
            black_plugin,
            "_run_subprocess",
            return_value=(False, output),
        ),
        patch.object(black_plugin, "_check_line_length_violations", return_value=[]),
    ):
        result = black_plugin.check([str(py_file)], {})

    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_equal_to(1)
    assert_that(result.issues[0].file).ends_with("module.py")
    assert_that(result.issues[0].message).contains("Would reformat")


def test_fix_reformats_and_rechecks(
    black_plugin: BlackPlugin,
    tmp_path: Path,
) -> None:
    """Fix applies formatting and reports the initial/fixed/remaining counts.

    Args:
        black_plugin: The BlackPlugin instance to test.
        tmp_path: Temporary directory path for test files.
    """
    py_file = tmp_path / "module.py"
    py_file.write_text("x=1\n")
    check_output = f"would reformat {py_file}\n"

    with (
        patch.object(
            black_plugin,
            "_run_subprocess",
            side_effect=[
                (False, check_output),
                (True, f"reformatted {py_file}\n"),
                (True, ""),
            ],
        ),
        patch.object(
            black_plugin,
            "_check_line_length_violations",
            return_value=[],
        ),
    ):
        result = black_plugin.fix([str(py_file)], {})

    assert_that(result.success).is_true()
    assert_that(result.initial_issues_count).is_equal_to(1)
    assert_that(result.fixed_issues_count).is_equal_to(1)
    assert_that(result.remaining_issues_count).is_equal_to(0)
