"""Tests with real RuffPlugin instance."""

from __future__ import annotations

import subprocess  # nosec B404 - constructs a timeout exception only
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest
from assertpy import assert_that

from lintro.enums.tool_name import ToolName
from lintro.tools.ruff.check import execute_ruff_check

if TYPE_CHECKING:
    from lintro.tools.ruff.definition import RuffPlugin


def test_ruff_parse_warning_names_rejected_records(
    ruff_plugin: RuffPlugin,
    temp_python_file: str,
    loguru_records: list[tuple[str, str]],
) -> None:
    """A zero-exit rejected report is diagnosed as a parse failure."""
    ruff_plugin.options["format_check"] = False
    with patch(
        "lintro.tools.ruff.check.run_subprocess_with_timeout",
        return_value=(True, "[{}]"),
    ):
        result = execute_ruff_check(ruff_plugin, [temp_python_file])

    warnings = "\n".join(
        message for level, message in loguru_records if level == "WARNING"
    )
    assert_that(result.success).is_false()
    assert_that(warnings).contains(
        "1 diagnostic record(s) could not be parsed",
    )
    assert_that(warnings).does_not_contain("subprocess failed")


@pytest.mark.parametrize("success", [True, False])
@pytest.mark.parametrize(
    ("output", "expected_issues"),
    [
        ("[{}]", 0),
        ('[{"filename": "a.py"}]', 0),
        ('{}\n{"filename": "a.py", "message": "bad syntax", "code": null}', 1),
    ],
)
def test_ruff_check_counts_rejected_records(
    ruff_plugin: RuffPlugin,
    temp_python_file: str,
    success: bool,
    output: str,
    expected_issues: int,
) -> None:
    """Malformed records are counted even when the subprocess exits zero."""
    ruff_plugin.options["format_check"] = False
    with patch(
        "lintro.tools.ruff.check.run_subprocess_with_timeout",
        return_value=(success, output),
    ):
        result = execute_ruff_check(ruff_plugin, [temp_python_file])

    assert_that(result.parse_failures_count).is_equal_to(1)
    assert_that(result.issues_count).is_equal_to(expected_issues)
    assert_that(result.issues).is_length(expected_issues)
    assert_that(result.success).is_false()


def test_ruff_parse_failure_survives_format_timeout(
    ruff_plugin: RuffPlugin,
    temp_python_file: str,
) -> None:
    """A later formatter timeout retains the rejected lint record count."""
    ruff_plugin.options["format_check"] = True
    with patch(
        "lintro.tools.ruff.check.run_subprocess_with_timeout",
        side_effect=[
            (True, "[{}]"),
            subprocess.TimeoutExpired(cmd=["ruff", "format"], timeout=30),
        ],
    ):
        result = execute_ruff_check(ruff_plugin, [temp_python_file])

    assert_that(result.timed_out).is_true()
    assert_that(result.parse_failures_count).is_equal_to(1)
    assert_that(result.issues_count).is_zero()
    assert_that(result.success).is_false()


def test_execute_ruff_check_with_real_plugin_no_files(
    ruff_plugin: RuffPlugin,
) -> None:
    """Execute with real plugin when no files to check.

    Args:
        ruff_plugin: RuffPlugin instance for testing.
    """
    with patch.object(ruff_plugin, "_verify_tool_version", return_value=None):
        result = execute_ruff_check(ruff_plugin, [])

        assert_that(result.success).is_true()
        assert_that(result.output).is_equal_to("No files to check.")


def test_execute_ruff_check_with_real_plugin(
    ruff_plugin: RuffPlugin,
    temp_python_file: str,
) -> None:
    """Execute with real plugin and temp file.

    Args:
        ruff_plugin: RuffPlugin instance for testing.
        temp_python_file: Temporary Python file for testing.
    """
    with (
        patch.object(ruff_plugin, "_verify_tool_version", return_value=None),
        patch(
            "lintro.tools.ruff.check.run_subprocess_with_timeout",
            return_value=(True, "[]"),
        ),
    ):
        result = execute_ruff_check(ruff_plugin, [temp_python_file])

        assert_that(result.success).is_true()
        assert_that(result.name).is_equal_to(ToolName.RUFF)
