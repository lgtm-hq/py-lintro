"""Tests for pydoclint plugin check method."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

from assertpy import assert_that

from lintro.tools.definitions.pydoclint import PydoclintPlugin


def test_check_with_mocked_subprocess_success(
    pydoclint_plugin: PydoclintPlugin,
    tmp_path: Path,
) -> None:
    """Check returns success when no issues found.

    Args:
        pydoclint_plugin: The PydoclintPlugin instance to test.
        tmp_path: Temporary directory path for test files.
    """
    test_file = tmp_path / "test_module.py"
    test_file.write_text('"""Test module."""\n')

    with (
        patch(
            "lintro.plugins.execution_preparation.verify_tool_version",
            return_value=None,
        ),
        patch.object(
            pydoclint_plugin,
            "_run_subprocess",
            return_value=(True, ""),
        ),
    ):
        result = pydoclint_plugin.check([str(test_file)], {})

    assert_that(result.success).is_true()
    assert_that(result.issues_count).is_equal_to(0)


def test_check_with_mocked_subprocess_issues(
    pydoclint_plugin: PydoclintPlugin,
    tmp_path: Path,
) -> None:
    """Check returns issues when pydoclint finds problems.

    Args:
        pydoclint_plugin: The PydoclintPlugin instance to test.
        tmp_path: Temporary directory path for test files.
    """
    test_file = tmp_path / "test_module.py"
    test_file.write_text('def foo():\n    """Missing return."""\n    return 1\n')

    pydoclint_output = (
        f"{test_file}\n"
        "    1: DOC201: Function `foo` does not have a "
        "return section in docstring"
    )

    with (
        patch(
            "lintro.plugins.execution_preparation.verify_tool_version",
            return_value=None,
        ),
        patch.object(
            pydoclint_plugin,
            "_run_subprocess",
            return_value=(False, pydoclint_output),
        ),
    ):
        result = pydoclint_plugin.check([str(test_file)], {})

    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_greater_than(0)


def test_check_with_timeout(
    pydoclint_plugin: PydoclintPlugin,
    tmp_path: Path,
) -> None:
    """Check handles timeout correctly.

    Args:
        pydoclint_plugin: The PydoclintPlugin instance to test.
        tmp_path: Temporary directory path for test files.
    """
    test_file = tmp_path / "test_module.py"
    test_file.write_text('"""Test module."""\n')

    with (
        patch(
            "lintro.plugins.execution_preparation.verify_tool_version",
            return_value=None,
        ),
        patch.object(
            pydoclint_plugin,
            "_run_subprocess",
            side_effect=subprocess.TimeoutExpired(cmd=["pydoclint"], timeout=30),
        ),
    ):
        result = pydoclint_plugin.check([str(test_file)], {})

    assert_that(result.success).is_false()


def test_check_with_no_python_files(
    pydoclint_plugin: PydoclintPlugin,
    tmp_path: Path,
) -> None:
    """Check returns success when no Python files found.

    Args:
        pydoclint_plugin: The PydoclintPlugin instance to test.
        tmp_path: Temporary directory path for test files.
    """
    non_py_file = tmp_path / "test.txt"
    non_py_file.write_text("Not a python file")

    with patch(
        "lintro.plugins.execution_preparation.verify_tool_version",
        return_value=None,
    ):
        result = pydoclint_plugin.check([str(non_py_file)], {})

    assert_that(result.success).is_true()
    assert_that(result.output).contains("No .py/.pyi files found")
