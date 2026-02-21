"""Unit tests for tsc plugin execution."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
from assertpy import assert_that

from lintro.tools.definitions.tsc import TscPlugin

# =============================================================================
# Tests for TscPlugin.check method
# =============================================================================


def test_check_with_mocked_subprocess_success(
    tsc_plugin: TscPlugin,
    tmp_path: Path,
) -> None:
    """Check returns success when no issues found.

    Args:
        tsc_plugin: The TscPlugin instance to test.
        tmp_path: Temporary directory path for test files.
    """
    test_file = tmp_path / "main.ts"
    test_file.write_text("const x: number = 42;\n")

    with (
        patch(
            "lintro.plugins.execution_preparation.verify_tool_version",
            return_value=None,
        ),
        patch.object(
            tsc_plugin,
            "_run_subprocess",
            return_value=(True, ""),
        ),
    ):
        result = tsc_plugin.check([str(test_file)], {})

    assert_that(result.success).is_true()
    assert_that(result.issues_count).is_equal_to(0)


def test_check_with_mocked_subprocess_issues(
    tsc_plugin: TscPlugin,
    tmp_path: Path,
) -> None:
    """Check returns issues when tsc finds type errors.

    Args:
        tsc_plugin: The TscPlugin instance to test.
        tmp_path: Temporary directory path for test files.
    """
    test_file = tmp_path / "main.ts"
    test_file.write_text("const x: number = 'string';\n")

    tsc_output = (
        f"{test_file}(1,7): error TS2322:"
        " Type 'string' is not assignable to type 'number'."
    )

    with (
        patch(
            "lintro.plugins.execution_preparation.verify_tool_version",
            return_value=None,
        ),
        patch.object(
            tsc_plugin,
            "_run_subprocess",
            return_value=(False, tsc_output),
        ),
    ):
        result = tsc_plugin.check([str(test_file)], {})

    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_greater_than(0)


def test_check_with_timeout(
    tsc_plugin: TscPlugin,
    tmp_path: Path,
) -> None:
    """Check handles timeout correctly.

    Args:
        tsc_plugin: The TscPlugin instance to test.
        tmp_path: Temporary directory path for test files.
    """
    test_file = tmp_path / "main.ts"
    test_file.write_text("const x: number = 42;\n")

    with (
        patch(
            "lintro.plugins.execution_preparation.verify_tool_version",
            return_value=None,
        ),
        patch.object(
            tsc_plugin,
            "_run_subprocess",
            side_effect=subprocess.TimeoutExpired(cmd=["tsc"], timeout=60),
        ),
    ):
        result = tsc_plugin.check([str(test_file)], {})

    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_greater_than(0)


def test_check_with_no_typescript_files(
    tsc_plugin: TscPlugin,
    tmp_path: Path,
) -> None:
    """Check returns success when no TypeScript files found.

    Args:
        tsc_plugin: The TscPlugin instance to test.
        tmp_path: Temporary directory path for test files.
    """
    non_ts_file = tmp_path / "test.txt"
    non_ts_file.write_text("Not a TypeScript file")

    with patch(
        "lintro.plugins.execution_preparation.verify_tool_version",
        return_value=None,
    ):
        result = tsc_plugin.check([str(non_ts_file)], {})

    assert_that(result.success).is_true()
    assert_that(result.output).contains("No .ts/.tsx/.mts/.cts files")


def test_check_parses_multiple_issues(
    tsc_plugin: TscPlugin,
    tmp_path: Path,
) -> None:
    """Check correctly parses multiple issues from output.

    Args:
        tsc_plugin: The TscPlugin instance to test.
        tmp_path: Temporary directory path for test files.
    """
    test_file = tmp_path / "main.ts"
    test_file.write_text("const x: number = 'a';\nconst y: string = 42;\n")

    tsc_output = (
        f"{test_file}(1,7): error TS2322:"
        " Type 'string' is not assignable to type 'number'.\n"
        f"{test_file}(2,7): error TS2322:"
        " Type 'number' is not assignable to type 'string'."
    )

    with (
        patch(
            "lintro.plugins.execution_preparation.verify_tool_version",
            return_value=None,
        ),
        patch.object(
            tsc_plugin,
            "_run_subprocess",
            return_value=(False, tsc_output),
        ),
    ):
        result = tsc_plugin.check([str(test_file)], {})

    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_equal_to(2)


# =============================================================================
# Tests for TscPlugin.fix method
# =============================================================================


def test_fix_raises_not_implemented(tsc_plugin: TscPlugin) -> None:
    """Fix raises NotImplementedError.

    Args:
        tsc_plugin: The TscPlugin instance to test.
    """
    with pytest.raises(NotImplementedError, match="cannot automatically fix"):
        tsc_plugin.fix(paths=["src"], options={})
