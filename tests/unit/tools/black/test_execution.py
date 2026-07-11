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

    with patch.object(black_plugin, "_run_subprocess", return_value=(True, "")):
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

    with patch.object(
        black_plugin,
        "_run_subprocess",
        return_value=(False, output),
    ):
        result = black_plugin.check([str(py_file)], {})

    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_greater_than(0)
