"""Tests for mypy skipping cleanly when no Python files are in scope.

Covers issues #1071 and #930: running mypy against a repository or scope with
zero ``.py``/``.pyi`` files must no-op like other language tools rather than
hard-erroring with ``There are no .py[i] files in directory '.'``.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from assertpy import assert_that

from lintro.tools.definitions.mypy import (
    MypyPlugin,
    _has_no_python_files_error,
)
from tests.test_samples_helpers import copy_sample


@pytest.mark.parametrize(
    ("output", "expected"),
    [
        ("There are no .py[i] files in directory '.'", True),
        ("There are no .py[i] files in directory 'src'", True),
        ("mypy: error: There are no .py[i] files in directory '.'\n", True),
        ("NO .PY[I] FILES", True),
        ("", False),
        ("[]", False),
        ('[{"path": "a.py", "line": 1, "message": "x"}]', False),
    ],
    ids=[
        "current_dir",
        "named_dir",
        "with_prefix",
        "case_insensitive",
        "empty",
        "empty_json_array",
        "real_issue_payload",
    ],
)
def test_has_no_python_files_error(*, output: str, expected: bool) -> None:
    """Detect mypy's no-Python-files diagnostic in combined output.

    Args:
        output: Combined stdout/stderr captured from the mypy subprocess.
        expected: Whether the diagnostic should be detected.
    """
    result = _has_no_python_files_error(output)
    assert_that(result).is_equal_to(expected)


def test_check_empty_directory_skips_cleanly(
    mypy_plugin: MypyPlugin,
    tmp_path: Path,
) -> None:
    """Empty directory yields a clean pass with zero issues.

    Args:
        mypy_plugin: The MypyPlugin instance to test.
        tmp_path: Temporary directory path with no files.
    """
    with patch(
        "lintro.plugins.execution_preparation.verify_tool_version",
        return_value=None,
    ):
        result = mypy_plugin.check([str(tmp_path)], {})

    assert_that(result.success).is_true()
    assert_that(result.issues_count).is_equal_to(0)
    assert_that(result.issues).is_none()


def test_check_only_non_python_files_skips_cleanly(
    mypy_plugin: MypyPlugin,
    tmp_path: Path,
) -> None:
    """Directory with only non-Python files yields a clean pass.

    Args:
        mypy_plugin: The MypyPlugin instance to test.
        tmp_path: Temporary directory path for test files.
    """
    (tmp_path / "README.md").write_text("# Readme\n")
    (tmp_path / "config.yaml").write_text("key: value\n")

    with patch(
        "lintro.plugins.execution_preparation.verify_tool_version",
        return_value=None,
    ):
        result = mypy_plugin.check([str(tmp_path)], {})

    assert_that(result.success).is_true()
    assert_that(result.issues_count).is_equal_to(0)
    assert_that(result.issues).is_none()


def test_check_no_python_files_error_output_skips_cleanly(
    mypy_plugin: MypyPlugin,
    tmp_path: Path,
) -> None:
    """Mypy's no-Python-files diagnostic is treated as a clean skip.

    This exercises the defensive secondary guard for the execution path where
    mypy performs its own directory discovery (the Docker/reusable-workflow
    path) and emits the diagnostic itself rather than lintro pre-empting it.

    Args:
        mypy_plugin: The MypyPlugin instance to test.
        tmp_path: Temporary directory path for test files.
    """
    # A real Python file makes lintro's pre-flight discovery pass so that mypy
    # is actually invoked; the mocked subprocess then simulates mypy resolving
    # an empty scope on its own.
    copy_sample(
        tmp_path,
        "tools",
        "python",
        "mypy",
        "mypy_module.py",
        dest_name="module.py",
    )

    with patch(
        "lintro.plugins.execution_preparation.verify_tool_version",
        return_value=None,
    ):
        with patch.object(
            mypy_plugin,
            "_run_subprocess",
            return_value=(False, "There are no .py[i] files in directory '.'"),
        ):
            result = mypy_plugin.check([str(tmp_path)], {})

    assert_that(result.success).is_true()
    assert_that(result.issues_count).is_equal_to(0)
    assert_that(result.issues).is_none()
    assert_that(result.output).contains("No .py/.pyi files found")


def test_check_python_file_clean_run_unchanged(
    mypy_plugin: MypyPlugin,
    tmp_path: Path,
) -> None:
    """A directory with a Python file and no issues passes normally.

    Args:
        mypy_plugin: The MypyPlugin instance to test.
        tmp_path: Temporary directory path for test files.
    """
    copy_sample(
        tmp_path,
        "tools",
        "python",
        "mypy",
        "mypy_module.py",
        dest_name="module.py",
    )

    with patch(
        "lintro.plugins.execution_preparation.verify_tool_version",
        return_value=None,
    ):
        with patch.object(
            mypy_plugin,
            "_run_subprocess",
            return_value=(True, "[]"),
        ):
            result = mypy_plugin.check([str(tmp_path)], {})

    assert_that(result.success).is_true()
    assert_that(result.issues_count).is_equal_to(0)


def test_check_python_file_with_issue_still_fails(
    mypy_plugin: MypyPlugin,
    tmp_path: Path,
) -> None:
    """A real type error is still reported when Python files are present.

    Args:
        mypy_plugin: The MypyPlugin instance to test.
        tmp_path: Temporary directory path for test files.
    """
    copy_sample(
        tmp_path,
        "tools",
        "python",
        "mypy",
        "mypy_module.py",
        dest_name="module.py",
    )
    payload = (
        '[{"path": "module.py", "line": 1, "column": 0, '
        '"severity": "error", "message": "boom", "code": "misc"}]'
    )

    with patch(
        "lintro.plugins.execution_preparation.verify_tool_version",
        return_value=None,
    ):
        with patch.object(
            mypy_plugin,
            "_run_subprocess",
            return_value=(False, payload),
        ):
            result = mypy_plugin.check([str(tmp_path)], {})

    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_equal_to(1)


def test_check_marker_text_with_issue_does_not_suppress(
    mypy_plugin: MypyPlugin,
    tmp_path: Path,
) -> None:
    """The skip guard never suppresses real issues in mixed output.

    Even if the ``no .py[i] files`` marker text appears alongside parsed
    issues, the run must still report the issues rather than skip.

    Args:
        mypy_plugin: The MypyPlugin instance to test.
        tmp_path: Temporary directory path for test files.
    """
    copy_sample(
        tmp_path,
        "tools",
        "python",
        "mypy",
        "mypy_module.py",
        dest_name="module.py",
    )
    payload = (
        '[{"path": "module.py", "line": 1, "column": 0, '
        '"severity": "error", "message": "no .py[i] files", "code": "misc"}]'
    )

    with patch(
        "lintro.plugins.execution_preparation.verify_tool_version",
        return_value=None,
    ):
        with patch.object(
            mypy_plugin,
            "_run_subprocess",
            return_value=(False, payload),
        ):
            result = mypy_plugin.check([str(tmp_path)], {})

    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_equal_to(1)
